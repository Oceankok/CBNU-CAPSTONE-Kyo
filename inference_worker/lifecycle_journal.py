"""Transactional, opt-in lifecycle bridge. Not connected to the live runner.

Timestamp input must already be verified common UTC seconds for wire payloads.
The caller owns conversion/calibration. One owner thread serializes this bridge
and outbox/dispatcher. No durable state is silently replayed into a new tracker.
"""
from copy import deepcopy
from math import isfinite
from inference_worker.event_lifecycle import EventLifecycle
from inference_worker.continuous_payloads import start_payload, update_payload


class LifecycleJournal:
    def __init__(self, outbox, *, camera_id, camera_snapshot, model_version,
                 missing_grace_seconds, threshold_seconds, max_sample_gap_seconds, progress_emit_interval_seconds):
        if not isinstance(model_version,str) or not model_version:
            raise ValueError('explicit model version required')
        if (isinstance(progress_emit_interval_seconds,bool) or not isinstance(progress_emit_interval_seconds,(int,float))
                or not isfinite(progress_emit_interval_seconds) or progress_emit_interval_seconds < 0):
            raise ValueError('explicit nonnegative progress cadence required')
        self.progress_interval=progress_emit_interval_seconds
        self.last_emitted={}
        snapshot=deepcopy(camera_snapshot)
        ids=[x['camera_id'] if isinstance(x,dict) else x for x in snapshot]
        if not snapshot or camera_id not in ids or len(set(ids))!=len(ids):
            raise ValueError('camera snapshot must contain unique owner camera')
        self.outbox=outbox
        self.snapshot=snapshot
        self.model_version=model_version
        self.lifecycle=EventLifecycle(camera_id,missing_grace_seconds=missing_grace_seconds,
            threshold_seconds=threshold_seconds,max_sample_gap_seconds=max_sample_gap_seconds)
        self.api_sequences={}
        self.capture_context_id=None
        self.capture_context_bound=False

    def bind_capture_context(self,context_id):
        """Freeze source attribution before processing the first frame.

        A changed context requires a new runner/tracker, never rebinding an old
        journal. None is a legacy binding and cannot later upgrade in place.
        """
        import re
        if context_id is not None and (not isinstance(context_id,str) or
                not re.fullmatch('[A-Za-z0-9_-]{1,100}',context_id)):
            raise ValueError('capture_context_id')
        if self.capture_context_bound and self.capture_context_id!=context_id:
            raise ValueError('capture_context_changed_requires_new_session')
        if not self.capture_context_bound and (self.api_sequences or self.lifecycle.last_timestamp is not None):
            raise ValueError('capture_context_must_bind_before_observations')
        self.capture_context_id=context_id
        self.capture_context_bound=True

    def _commit(self,candidate,transitions):
        sequences=dict(self.api_sequences)
        emitted=dict(self.last_emitted)
        operations=[]
        for transition in transitions:
            event=transition['event_id'];kind=transition['kind']
            if kind=='start':
                body=start_payload(transition,session_id=self.outbox.session,model_version=self.model_version,
                    capture_context_id=self.capture_context_id)
                operations.append(dict(kind='start',event_id=event,camera_snapshot=deepcopy(self.snapshot),
                    idempotency_key=event+':start',body=body))
                sequences[event]=0
                emitted[event]=transition['end_time']
            else:
                seq=sequences[event]+1
                if kind=='progress':
                    if transition['end_time']-emitted[event] < self.progress_interval:
                        continue
                    body=update_payload(transition,session_id=self.outbox.session,sequence=seq)
                    operations.append(dict(kind=kind,event_id=event,api_sequence=seq,
                        idempotency_key=event+':update:'+str(seq),body=body))
                    sequences[event]=seq
                    emitted[event]=transition['end_time']
                elif kind=='close':
                    if transition['end_time'] > emitted[event]:
                        final_progress={**transition,'kind':'progress'}
                        body=update_payload(final_progress,session_id=self.outbox.session,sequence=seq)
                        operations.append(dict(kind='progress',event_id=event,api_sequence=seq,
                            idempotency_key=event+':update:'+str(seq),body=body,final_progress=True))
                        seq+=1
                    # Capture the unchanged final observation, not a premature
                    # HTTP close. Manifest completion supplies complete/count later.
                    operations.append(dict(kind='pending_close',event_id=event,
                        api_sequence=seq,transition=deepcopy(transition)))
                    sequences.pop(event)
                    emitted.pop(event)
                else:
                    raise ValueError('unknown transition')
        self.outbox.apply_lifecycle_batch(operations)
        self.lifecycle=candidate
        self.api_sequences=sequences
        self.last_emitted=emitted
        return transitions

    def update(self, assessments, timestamp, *, departed_tracking_ids=()):
        candidate=deepcopy(self.lifecycle)
        transitions=candidate.update(assessments,timestamp,departed_tracking_ids=departed_tracking_ids)
        return self._commit(candidate,transitions)

    def close_all(self,reason):
        candidate=deepcopy(self.lifecycle)
        return self._commit(candidate,candidate.close_all(reason))
