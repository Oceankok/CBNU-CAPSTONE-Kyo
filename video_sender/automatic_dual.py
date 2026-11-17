"""Separate two-camera clock-proof runner; original launcher and settings are read-only."""
import argparse
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
from uuid import uuid4

from clock_contract import ClockPolicy
from video_sender.context_sender import atomic_json
from video_sender.context_starter import Starter, launcher_lock


def flatten(value):
    if isinstance(value,list):
        return [item for child in value for item in flatten(child)]
    if isinstance(value,dict):return [value]
    raise ValueError('invalid_dual_settings')


def load_original(path):
    spec=importlib.util.spec_from_file_location('_original_camera_launcher',str(path))
    if spec is None or spec.loader is None:raise ValueError('launcher_unavailable')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    if not callable(getattr(module,'validate',None)) or not callable(getattr(module,'read_token',None)):
        raise ValueError('launcher_api_unavailable')
    return module


@contextmanager
def child_environment(values):
    previous={key:os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key,value in previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value


class AutomaticDual:
    def __init__(self,settings_path,loader,policy_path,*,repository=None,starter_factory=Starter):
        self.repository=Path(repository or Path(__file__).resolve().parents[1]).resolve()
        entries=flatten(json.loads(Path(settings_path).read_text(encoding='utf-8-sig')))
        if len(entries)!=2 or any(not {'config','output','index','repo','retain_source'} <= set(x) for x in entries):
            raise ValueError('two_camera_settings_required')
        policy=json.loads(Path(policy_path).read_text(encoding='utf-8-sig'))
        if set(policy)!={'server_epoch','clock_policy','refresh_seconds','sources','archive_max_bytes'}:raise ValueError('source_policy_invalid')
        ClockPolicy(**policy['clock_policy'])
        if type(policy['archive_max_bytes']) is not int or policy['archive_max_bytes']<=0:raise ValueError('archive_limit_required')
        if not isinstance(policy['server_epoch'],str) or not policy['server_epoch']:raise ValueError('source_epoch_required')
        refresh=policy['refresh_seconds']
        if isinstance(refresh,bool) or not isinstance(refresh,(int,float)) or not 0<refresh<policy['clock_policy']['ttl_seconds']/2:
            raise ValueError('source_refresh_invalid')
        if not isinstance(policy['sources'],dict):raise ValueError('source_scope_invalid')
        self.slots=[];self.loader=loader;self.policy=policy
        for entry in entries:
            repo,config,output,settings,path=loader.validate(entry)
            camera=settings['camera_id'];node=policy['sources'].get(camera)
            if not isinstance(node,str) or not node or len(node)>160:raise ValueError('source_node_required')
            output=Path(output).resolve();root=output/'automatic-context-sessions'
            state_path=output/'automatic-context-state.json'
            starter=starter_factory(root,repository=self.repository)
            if state_path.exists():
                state=json.loads(state_path.read_text(encoding='utf-8'))
                if (set(state) not in ({'directory','camera_id','index'}, {'directory','camera_id','index','pre_session_failure'})
                    or state.get('pre_session_failure',True) is not True
                    or state['camera_id']!=camera or state['index']!=int(entry['index'])):
                    raise ValueError('saved_session_binding_mismatch')
                directory=Path(state['directory']).resolve()
                if directory.parent != root.resolve():raise ValueError('saved_session_path_invalid')
                if state.get('pre_session_failure') is True:
                    if (directory/'session.json').exists():raise ValueError('saved_failure_conflicts_with_session')
                else:starter.directory=directory
            self.slots.append(dict(entry=entry,config=config,output=output,settings=settings,path=path,
                                   camera=camera,node=node,starter=starter,state_path=state_path))
        if len({slot['camera'] for slot in self.slots})!=2 or len({str(slot['output']) for slot in self.slots})!=2:
            raise ValueError('camera_or_output_duplicate')
        if set(policy['sources'])!={s['camera'] for s in self.slots}:raise ValueError('source_scope_mismatch')

    def environment(self,slot):
        return {'PATH':slot['path'],'VIDEO_SERVER_URL':slot['settings']['server_url'],
            'VIDEO_CAMERA_ID':slot['camera'],'VIDEO_NODE_TOKEN':self.loader.read_token(slot['config']),
            'VIDEO_CLOCK_PROOF_ENABLED':'true','VIDEO_CLOCK_SERVER_EPOCH':self.policy['server_epoch'],
            'VIDEO_CLOCK_SOURCE_NODE_ID':slot['node'],
            'VIDEO_CLOCK_POLICY_JSON':json.dumps(self.policy['clock_policy'],separators=(',',':')),
            'VIDEO_CLOCK_REFRESH_SECONDS':str(self.policy['refresh_seconds']),
            'VIDEO_AUTOMATIC_ARCHIVE_DIR':str(slot['output']/'automatic-source-archive'),
            'VIDEO_AUTOMATIC_ARCHIVE_MAX_BYTES':str(self.policy['archive_max_bytes'])}

    def record_pre_session_failure(self,slot):
        starter=slot['starter']
        if starter.pre_session_failure():
            atomic_json(slot['state_path'],{'directory':str(starter.directory),'camera_id':slot['camera'],
                'index':int(slot['entry']['index']),'pre_session_failure':True})
            return True
        return False

    def start(self,slot,mode='new'):
        starter=slot['starter'];entry=slot['entry']
        if mode=='new':
            if starter.directory is not None:
                # No missing journal or PID is accepted as stopped.
                try:closed=starter.journal()['closed']
                except FileNotFoundError:
                    if not self.record_pre_session_failure(slot):raise
                    starter.directory=None
                else:
                    if not closed:raise ValueError('prior_session_unresolved')
            target=starter.root/str(uuid4())
            slot['output'].mkdir(parents=True,exist_ok=True)
            atomic_json(slot['state_path'],{'directory':str(target),'camera_id':slot['camera'],'index':int(entry['index'])})
        elif mode=='resume-drain':
            target=starter.directory
            if target is None:raise ValueError('no_recovery_session')
        else:raise ValueError('unsupported_mode')
        with child_environment(self.environment(slot)):
            starter.start(target,mode=mode,camera_index=int(entry['index']))

    def start_all(self):
        started=[]
        try:
            for slot in self.slots:
                self.start(slot);started.append(slot)
        except Exception as error:
            for slot in started:
                try:self.stop(slot)
                except Exception:pass
            raise RuntimeError('partial_start_stopping' if started else 'start_failed') from error

    def stop(self,slot):
        starter=slot['starter']
        if starter.active():starter.request_stop()
        elif starter.directory is not None:
            # A GUI restart loses the child handle, not the cooperative run ID.
            if starter.journal()['closed']:raise ValueError('already_closed')
            starter._publish_stop()
        else:raise ValueError('no_session')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--settings',required=True);parser.add_argument('--launcher',required=True);parser.add_argument('--policy',required=True)
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    import tkinter as tk
    from tkinter import ttk,messagebox
    with launcher_lock():
        try:controller=AutomaticDual(args.settings,load_original(args.launcher),args.policy)
        except Exception:
            raise SystemExit('설정 또는 실행 환경을 확인하세요. 촬영은 시작되지 않았습니다.') from None
        if args.check:
            print('local_configuration_valid_not_server_proof')
            for slot in controller.slots:print(slot['camera'],int(slot['entry']['index']))
            return
        window=tk.Tk();window.title('Kyo 자동 수집')
        statuses=[tk.StringVar() for _ in controller.slots]
        for slot,status in zip(controller.slots,statuses):
            ttk.Label(window,text=slot['camera']).pack();ttk.Label(window,textvariable=status,wraplength=600).pack()
        def action(operation):
            if operation=='new':
                try:controller.start_all()
                except RuntimeError as error:
                    messagebox.showerror('시작 실패', '일부 카메라 시작 후 종료 요청 중입니다. 종료 ACK와 세션 복구를 확인하세요.' if str(error)=='partial_start_stopping' else '설정과 세션 상태를 확인하세요.')
                return
            for slot in controller.slots:
                try:
                    if operation=='stop':controller.stop(slot)
                    else:controller.start(slot,mode=operation)
                except Exception:messagebox.showerror('처리 불가',slot['camera']+' 설정과 세션 상태를 확인하세요. 기존 영상은 보존됩니다.')
        buttons=ttk.Frame(window);buttons.pack(pady=12)
        for label,operation in [('두 카메라 시작','new'),('모두 종료 요청','stop'),('남은 전송 복구','resume-drain')]:
            ttk.Button(buttons,text=label,command=lambda op=operation:action(op)).pack(side='left')
        def refresh():
            for slot,status in zip(controller.slots,statuses):
                try:
                    controller.record_pre_session_failure(slot)
                    status.set(slot['starter'].status())
                except Exception:status.set('세션 상태를 확인할 수 없습니다. 새 시작 전 복구가 필요합니다.')
            window.after(500,refresh)
        refresh();window.mainloop()


if __name__=='__main__':main()
