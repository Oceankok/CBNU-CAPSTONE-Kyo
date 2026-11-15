"""Original-session recovery; never restore tracks, rewrite bytes, or claim media ready."""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from inference_worker.continuous_payloads import _time
from backend.capture_grid import required_capture_end


class RecoveryRequired(RuntimeError):
    pass


def _decoded(row):
    return json.loads(row["body"])


class SessionRecovery:
    def __init__(self, box, dispatcher, *, endpoint, max_steps=64):
        self.box,self.dispatcher=box,dispatcher
        self.transport=dispatcher.transport
        self.endpoint=endpoint.rstrip("/")
        self.max_steps=max_steps

    def _read(self,event,session):
        value=self.transport.metadata_read("GET",f"/api/processing/continuous-events/{event}",
                                           params={"session_id":session})
        if value.get("event_id")!=event:
            raise RecoveryRequired("recovery_event_binding")
        return value

    def _receipt(self,event,body):
        value=self.transport.metadata_read("POST",
            f"/api/processing/continuous-events/{event}/operation-receipt",body=body)
        if (value.get("event_id")!=event or value.get("session_id")!=body["session_id"]
                or value.get("sequence")!=body["sequence"]
                or value.get("receipt_status") not in ("committed_exact","committed_different","not_committed")
                or not isinstance(value.get("recovery_blockers"),list)):
            raise RecoveryRequired("recovery_receipt_binding")
        if value["receipt_status"]=="committed_exact":
            accepted=value.get("accepted_operation")
            if (not isinstance(accepted,dict) or accepted.get("sha256")!=value.get("submitted_sha256")
                    or not re.fullmatch("[a-f0-9]{64}",str(value.get("submitted_sha256","")))
                    or accepted.get("body",{}).get("session_id")!=body["session_id"]
                    or accepted.get("body",{}).get("sequence")!=body["sequence"]):
                raise RecoveryRequired("recovery_exact_receipt_binding")
        return value

    def _historical_ack(self,event,row,receipt):
        body=_decoded(row)
        self.box.acknowledge(event,row["seq"],receipt={
            "event_id":event,"accepted_command":{"kind":body["kind"],"session_id":body["session_id"],
                "sequence":body["sequence"],"camera_id":None},"operation_receipt":receipt})

    def _source_present(self,event):
        rows=self.box.db.execute("SELECT * FROM segments WHERE event=?",(event,)).fetchall()
        return bool(rows) and all(row["state"]=="present" for row in rows)

    def _terminal_ambiguities(self,event,session):
        rows=self.box.db.execute("""SELECT * FROM requests r WHERE event=? AND ack IS NULL
            AND kind IN ('close','incomplete') AND NOT EXISTS(
            SELECT 1 FROM retired_requests t WHERE t.event=r.event AND t.seq=r.seq) ORDER BY seq""",(event,)).fetchall()
        for row in rows:
            body=_decoded(row);proof=self._receipt(event,body)
            if proof["receipt_status"]=="committed_exact":
                self._historical_ack(event,row,proof)
                # Exact is a historical ACK even if protection now prevents writes.
                if proof["recovery_blockers"]:raise RecoveryRequired("protected_historical_event")
                continue
            if proof["receipt_status"]=="committed_different" or proof["recovery_blockers"]:
                # If a late original complete won the CAS, retire only the
                # unaccepted superseding incomplete after proving that exact fact.
                original=self.box.db.execute("""SELECT r.* FROM retired_requests t
                    JOIN requests r ON r.event=t.event AND r.seq=t.seq
                    WHERE t.event=? AND t.reason='source_loss_uncommitted_complete' ORDER BY r.seq LIMIT 1""",
                    (event,)).fetchone()
                if original and body.get("complete") is False and not proof["recovery_blockers"]:
                    original_proof=self._receipt(event,_decoded(original))
                    if original_proof["receipt_status"]=="committed_exact":
                        with self.box.transaction():
                            self._historical_ack(event,original,original_proof)
                            self.box.retire_request(event,row["seq"],reason="late_original_committed",
                                                    receipt=original_proof)
                        continue
                raise RecoveryRequired("protected_or_conflicting_operation")
            if body.get("complete") is not True or self._source_present(event):
                continue  # Exact pending incomplete, or still-owned complete, may retry.
            remote=self._read(event,session)
            if (remote["lifecycle"]["phase"]!="open"
                    or remote["lifecycle"]["revision"]!=body["sequence"]-1
                    or proof.get("may_attempt_new_update") is not True):
                raise RecoveryRequired("recovery_revision_changed")
            count=sum(s["role"]=="clip" for s in remote["segments"])
            disposition={**body,"complete":False,"close_reason":"process_restart_evidence_unavailable",
                         "final_segment_count":count}
            # Atomic retirement+replacement leaves no crash window that loses work.
            with self.box.transaction():
                self.box.retire_request(event,row["seq"],reason="source_loss_uncommitted_complete",receipt=proof)
                self.box.enqueue(event,seq=self.box.event_status(event)["next_seq"],kind="incomplete",
                    idempotency_key=f"restart-disposition:{event}:{body['sequence']}",body=disposition)

    def _flush(self,event):
        for _ in range(self.max_steps):
            result=self.dispatcher.metadata_once(max_items=1,event_id=event)
            if not result or not any(state=="acknowledged" for _,state in result):break

    def _incomplete_open(self,event,session,remote):
        if remote["lifecycle"]["phase"]!="open":return
        if self.box.db.execute("""SELECT 1 FROM requests r WHERE event=? AND kind='incomplete'
                AND NOT EXISTS(SELECT 1 FROM retired_requests t WHERE t.event=r.event AND t.seq=r.seq)""",
                (event,)).fetchone():return
        start=self.box.db.execute("SELECT * FROM requests WHERE event=? AND kind='start'",(event,)).fetchone()
        latest=self.box.db.execute("SELECT * FROM requests WHERE event=? AND kind IN ('start','progress') ORDER BY seq DESC LIMIT 1",(event,)).fetchone()
        if start is None or latest is None:raise RecoveryRequired("missing_durable_observation")
        observation=_decoded(latest)
        body={k:observation[k] for k in ("timestamp_end","frame_sample_count","confidence",
                                        "observed_seconds","gap_seconds","gap_count")}
        body.update(session_id=session,sequence=remote["lifecycle"]["revision"]+1,kind="close",
                    close_reason="process_restart",complete=False,
                    final_segment_count=sum(s["role"]=="clip" for s in remote["segments"]))
        proof=self._receipt(event,body)
        if proof["recovery_blockers"] or proof["receipt_status"]=="committed_different":
            raise RecoveryRequired("protected_or_conflicting_operation")
        with self.box.transaction():
            self.box.enqueue(event,seq=self.box.event_status(event)["next_seq"],kind="incomplete",
                             idempotency_key=f"restart-close:{event}",body=body)
            self.box.db.execute("UPDATE pending_closes SET state='finalized' WHERE event=?",(event,))

    def _available_ranges(self,event,camera):
        self.box.reconcile()
        from datetime import datetime,timezone
        parse=lambda value:datetime.fromisoformat(value.replace("Z","+00:00")).timestamp()
        start,end=parse(camera["required_start_at"]),parse(camera["required_end_at"])
        result=[]
        for row in self.box.db.execute("SELECT * FROM segments WHERE event=? AND camera=? AND state='present' ORDER BY start",
                                       (event,camera["camera_id"])):
            if start-1e-6<=row["start"]<row["end"]<=end+1e-6:
                result.append({"start_at":_time(row["start"]),"end_at":_time(row["end"])})
        cursor=start
        for row in result:
            if abs(parse(row["start_at"])-cursor)>1e-6:return result,False
            cursor=parse(row["end_at"])
        return result,bool(result) and abs(cursor-end)<=1e-6

    def _recover_uploads(self,event,session,remote):
        from datetime import datetime
        from inference_worker.continuous_transport import UploadPlan
        parse=lambda value:datetime.fromisoformat(value.replace("Z","+00:00")).timestamp()
        status=self.box.event_status(event)
        if status["state"]!="closed" or remote["lifecycle"]["phase"]!="closed":return
        for row in self.box.db.execute("SELECT * FROM segments WHERE event=? AND state='present'",(event,)).fetchall():
            matches=[s for s in remote["segments"] if s["camera_id"]==row["camera"]
                     and abs(parse(s["capture_start_at"])-row["start"])<1e-6
                     and abs(parse(s["capture_end_at"])-row["end"])<1e-6]
            if len(matches)!=1:raise RecoveryRequired("restart_segment_binding")
            segment=matches[0]
            camera=next(c for c in remote["cameras"] if c["camera_id"]==row["camera"])
            path=self.box._path(row["path"])
            with path.open("rb") as stream:digest=hashlib.file_digest(stream,"sha256").hexdigest()
            plan=UploadPlan(event,segment["request_id"],row["camera"],row["path"],row["bytes"],digest,
                row["start"],row["end"],parse(camera["required_start_at"]),parse(camera["required_end_at"]),True)
            self.dispatcher.register_upload(row["id"],plan,session_id=session,role=segment["role"])
        for _ in range(self.max_steps):
            result=self.dispatcher.upload_once(max_items=1,event_id=event)
            if not result or any(state!="accepted_private" for _,state in result):break
        if self.dispatcher.db.execute("SELECT 1 FROM uploads WHERE event=? AND state!='accepted'",(event,)).fetchone():
            raise RecoveryRequired("restart_upload_pending")

    def _reports(self,event,session,remote):
        binding=self.box.db.execute("SELECT metadata FROM runtime_bindings WHERE session=?",(session,)).fetchone()
        if binding is None:raise RecoveryRequired("original_clock_binding_missing")
        provenance=json.loads(binding[0])
        if provenance["endpoint"].rstrip("/")!=self.endpoint:
            raise RecoveryRequired("original_endpoint_mismatch")
        available={c["camera_id"]:self._available_ranges(event,c) for c in remote["cameras"]}
        complete=(self.box.event_status(event)["state"]=="closed" and remote["lifecycle"]["phase"]=="closed"
                  and all(value[1] for value in available.values()))
        for camera in remote["cameras"]:
            name=camera["camera_id"];old=camera.get("capture_report")
            segments=[s for s in remote["segments"] if s["camera_id"]==name]
            if old and old["state"]=="incomplete":continue
            if old and old["state"]=="complete" and segments and all(s["status"]=="received" for s in segments):
                continue  # Existing server custody is never downgraded for local cleanup.
            pending=self.box.db.execute("SELECT 1 FROM requests WHERE event=? AND ack IS NULL AND idem LIKE ?",
                                       (event,name+":"+event+":restart-report%")).fetchone()
            if pending:continue
            clock=provenance["clocks"][name]
            sequence=(old["sequence"] if old else 0)+1
            missing=list(old.get("missing_ranges",[])) if old else []
            gap={"start_at":camera["required_start_at"],"end_at":camera["required_end_at"],
                 "reason":"process_restart_evidence_unavailable"}
            if not complete and gap not in missing:missing.append(gap)
            body=dict(session_id=session,sequence=sequence,state="complete" if complete else "incomplete",
                captured_ranges=old.get("captured_ranges",[]) if old else available[name][0],missing_ranges=missing,
                earliest_available_at=old.get("earliest_available_at") if old else (available[name][0][0]["start_at"] if available[name][0] else None),
                time_basis=old.get("time_basis","source_utc_corrected") if old else "source_utc_corrected",
                clock_offset_seconds=old.get("clock_offset_seconds",clock["offset"]) if old else clock["offset"],
                clock_uncertainty_seconds=old.get("clock_uncertainty_seconds",clock["error_bound"]) if old else clock["error_bound"],
                reason=None if complete else "process_restart_evidence_unavailable")
            self.box.enqueue(event,seq=self.box.event_status(event)["next_seq"],kind="capture_report",
                idempotency_key=f"{name}:{event}:restart-report:{sequence}",body=body)

    def _settled_history(self,eventrow):
        """Prove a released terminal history locally, without I/O or mutation.

        Queue-empty and a recovery marker are insufficient: every required
        camera and source needs an ACKed delivery or incomplete disposition.
        Retired/conflicting or ambiguous history stays on protected recovery.
        """
        from datetime import datetime
        stamp=lambda value:datetime.fromisoformat(value.replace("Z","+00:00")).timestamp()
        def covers(ranges,left,right):
            cursor=left
            for start,end in sorted(ranges):
                if start>cursor+1e-6:return False
                cursor=max(cursor,end)
                if cursor>=right-1e-6:return True
            return cursor>=right-1e-6
        event,session=eventrow["id"],eventrow["session"]
        if eventrow["state"] not in ("closed","incomplete"):return False
        complete=eventrow["state"]=="closed"
        if self.box.db.execute("SELECT 1 FROM retired_requests WHERE event=?",(event,)).fetchone():return False
        if self.box.db.execute("SELECT 1 FROM orphans WHERE event=?",(event,)).fetchone():return False
        intent=self.box.db.execute("SELECT * FROM pending_closes WHERE event=?",(event,)).fetchone()
        if not intent or intent["state"]!="finalized" or intent["session"]!=session:return False
        try:
            cameras=self.box._camera_ids(json.loads(eventrow["snapshot"]))
            if not cameras or len(set(cameras))!=len(cameras):return False
            rows=self.box.db.execute("SELECT * FROM requests WHERE event=? ORDER BY seq",(event,)).fetchall()
            if len(rows)!=eventrow["next_seq"] or [r["seq"] for r in rows]!=list(range(len(rows))):return False
            commands=[];reports={}
            for row in rows:
                if row["ack"] is None or hashlib.sha256(row["body"]).hexdigest()!=row["digest"]:return False
                body,ack=_decoded(row),json.loads(row["ack"])
                if row["kind"] not in ("start","progress","manifest","close","incomplete","capture_report"):return False
                camera=row["idem"].split(":",1)[0] if row["kind"]=="capture_report" else None
                expected=dict(kind="close" if row["kind"]=="incomplete" else row["kind"],session_id=session,
                    sequence=0 if row["kind"]=="start" else body.get("sequence"),camera_id=camera)
                if (body.get("session_id")!=session or ack.get("event_id")!=event
                        or ack.get("accepted_command")!=expected):return False
                if row["kind"]=="capture_report":
                    if camera not in cameras or body.get("state") not in ("complete","incomplete"):return False
                    reports[camera]=body
                commands.append((row["kind"],body,ack))
            starts=[b for k,b,a in commands if k=="start"]
            closes=[(k,b,a) for k,b,a in commands if k in ("close","incomplete")]
            if len(starts)!=1 or len(closes)!=1 or rows[0]["kind"]!="start":return False
            start=starts[0];kind,close,close_ack=closes[0]
            count=close.get("final_segment_count")
            phase=close_ack.get("lifecycle",{}).get("phase")
            if phase is None:
                # Lost-response recovery stores an authoritative operation proof,
                # rather than a normal mutation response's top-level lifecycle.
                proof=close_ack.get("operation_receipt",{})
                accepted=proof.get("accepted_operation",{})
                accepted_body=accepted.get("body",{})
                if (proof.get("receipt_status")!="committed_exact" or proof.get("event_id")!=event
                        or proof.get("session_id")!=session or proof.get("sequence")!=close.get("sequence")
                        or not re.fullmatch("[a-f0-9]{64}",str(proof.get("submitted_sha256","")))
                        or accepted.get("sha256")!=proof["submitted_sha256"]
                        or any((stamp(accepted_body[k])!=stamp(value) if k=="timestamp_end"
                                else accepted_body[k]!=value) for k,value in close.items())):return False
                if proof.get("lifecycle",{}).get("revision")!=close.get("sequence"):return False
                phase=proof.get("lifecycle",{}).get("phase")
            if (start.get("event_id")!=event or start.get("camera_id") not in cameras
                    or kind!=("close" if complete else "incomplete")
                    or close.get("kind")!="close" or close.get("complete") is not complete
                    or type(count) is not int or count<0 or (complete and count==0)
                    or set(reports)!=set(cameras) or close.get("sequence")!=intent["next_api_seq"]
                    or phase!=("closed" if complete else "interrupted")):return False
            if complete and any(r["state"]!="complete" for r in reports.values()):return False
            if not complete and not any(r["state"]=="incomplete" for r in reports.values()):return False
            required_start=stamp(start["timestamp_start"])-2
            required_end=required_capture_end(datetime.fromisoformat(start['timestamp_start'].replace('Z','+00:00')),
                datetime.fromisoformat(close['timestamp_end'].replace('Z','+00:00')),
                start.get('capture_output_fps')).timestamp()
            captured={};missing={}
            for camera,report in reports.items():
                captured[camera]=[(stamp(r["start_at"]),stamp(r["end_at"])) for r in report["captured_ranges"]]
                missing[camera]=[(stamp(r["start_at"]),stamp(r["end_at"])) for r in report["missing_ranges"]]
                ranges=captured[camera]+missing[camera]
                if (any(a<required_start-1e-6 or b>required_end+1e-6 or b<=a for a,b in ranges)
                        or not covers(ranges,required_start,required_end)
                        or (report["state"]=="complete" and missing[camera])
                        or (report["state"]=="incomplete" and not report.get("reason"))):return False
            manifests=[(b,a) for k,b,a in commands if k=="manifest"]
            if len(manifests)!=count or [b["sequence"] for b,a in manifests]!=list(range(count)):return False
            bindings={};cursor=required_start
            for body,ack in manifests:
                left,right=stamp(body["capture_start_at"]),stamp(body["capture_end_at"])
                if abs(left-cursor)>1e-6 or right<=left or right>required_end+1e-6:return False
                cursor=right
                current=[r for r in ack["segments"] if r["sequence"]==body["sequence"]]
                if len(current)!=len(cameras) or {r["camera_id"] for r in current}!=set(cameras):return False
                for remote in current:
                    camera=remote["camera_id"];role="clip" if camera==start["camera_id"] else "reference"
                    if (remote["event_id"]!=event or remote["role"]!=role
                            or stamp(remote["capture_start_at"])!=left
                            or stamp(remote["capture_end_at"])!=right):return False
                    bindings[(camera,left,right)]=remote
            if complete and abs(cursor-required_end)>1e-6:return False
            owners=self.box.db.execute("SELECT * FROM captures WHERE event=?",(event,)).fetchall()
            if len({r["camera"] for r in owners})!=len(owners) or any(r["camera"] not in cameras for r in owners):return False
            if complete and {r["camera"] for r in owners}!=set(cameras):return False
            spools={r["spool"]:r["camera"] for r in owners}
            for owner in owners:
                directory=self.box.root/owner["spool"]
                self.box._no_symlinks(directory)
                if owner["state"] not in ("active","interrupted") or directory.exists():return False
            segments=self.box.db.execute("SELECT * FROM segments WHERE event=?",(event,)).fetchall()
            uploads=self.dispatcher.db.execute("SELECT * FROM uploads WHERE event=?",(event,)).fetchall()
            plans={r["segment"]:r for r in uploads};delivered={c:[] for c in cameras};seen=set();used=set()
            for segment in segments:
                camera=segment["camera"]
                if (camera not in cameras or spools.get(segment["path"].split("/")[0])!=camera
                        or segment["cleanup"]!=0 or self.box._path(segment["path"]).exists()):return False
                if segment["state"]=="unavailable":
                    left=max(segment["start"],required_start);right=min(segment["end"],required_end)
                    if (complete or not segment["reason"] or segment["id"] in plans
                            or reports[camera]["state"]!="incomplete"
                            or (right>left and not covers(missing[camera],left,right))):return False
                    continue
                key=(camera,stamp(_time(segment["start"])),stamp(_time(segment["end"])))
                remote=bindings.get(key);upload=plans.get(segment["id"])
                if (not remote or key in seen or not upload or upload["state"]!="accepted"
                        or upload["session"]!=session or upload["role"]!=remote["role"]
                        or segment["state"]!="uploaded_private" or segment["reason"]!="verified_upload_receipt"
                        or not covers(captured[camera],segment["start"],segment["end"])):return False
                plan=json.loads(upload["plan"])
                if (plan["event_id"]!=event or plan["camera_id"]!=camera
                        or plan["request_id"]!=remote["request_id"] or plan["relative_path"]!=segment["path"]
                        or plan["capture_start"]!=segment["start"] or plan["capture_end"]!=segment["end"]
                        or abs(plan["required_start"]-required_start)>1e-6
                        or abs(plan["required_end"]-required_end)>1e-6
                        or plan["final_range_verified"] is not True
                        or not re.fullmatch("[a-f0-9]{64}",plan["sha256"])
                        or not self.dispatcher.cleanup_receipt_verified(dict(segment))):return False
                seen.add(key);used.add(segment["id"]);delivered[camera].append((segment["start"],segment["end"]))
            if used!=set(plans):return False
            for camera in cameras:
                if any(not covers(delivered[camera],a,b) for a,b in captured[camera]):return False
            for camera,left,right in bindings:
                if (camera,left,right) not in seen and (reports[camera]["state"]!="incomplete"
                        or not covers(missing[camera],left,right)):return False
            return True
        except (KeyError,TypeError,ValueError,AttributeError,OSError):
            return False

    def run(self):
        """Bounded recovery passes. Unknown/protected state blocks new frames."""
        old=self.box.db.execute("SELECT * FROM events WHERE session!=?",(self.box.session,)).fetchall()
        # Validate ALL old-session destinations before any global dispatcher
        # flush or authenticated read can send even one queued body.
        for row in old:
            saved=self.box.db.execute("SELECT metadata FROM runtime_bindings WHERE session=?",(row["session"],)).fetchone()
            if saved is None:
                self.box.recovery_state(row["id"],"required","original_endpoint_binding_missing")
                raise RecoveryRequired("original_endpoint_binding_missing")
            provenance=json.loads(saved[0])
            if (not isinstance(provenance.get("endpoint"),str)
                    or provenance["endpoint"].rstrip("/")!=self.endpoint):
                self.box.recovery_state(row["id"],"required","original_endpoint_mismatch")
                raise RecoveryRequired("original_endpoint_mismatch")
        for eventrow in old:
            event,session=eventrow["id"],eventrow["session"]
            # Normally completed history has no recovery marker. Skip only a
            # terminal, fully bound and released local settlement, even if a
            # prior recovery marker claimed success with obligations remaining.
            if self._settled_history(eventrow):continue
            try:
                historical=self.box.db.execute("SELECT * FROM requests WHERE event=? AND kind IN ('close','incomplete') AND ack IS NOT NULL ORDER BY seq DESC LIMIT 1",(event,)).fetchone()
                if historical and self._receipt(event,_decoded(historical))["recovery_blockers"]:
                    raise RecoveryRequired("protected_historical_event")
                for _ in range(3):
                    self._terminal_ambiguities(event,session)
                    self._flush(event)
                    remote=self._read(event,session)
                    self._incomplete_open(event,session,remote)
                    self._flush(event)
                    remote=self._read(event,session)
                    if remote["lifecycle"]["phase"]=="open":continue
                    self._reports(event,session,remote)
                    self._flush(event)
                    outstanding=self.box.db.execute("""SELECT 1 FROM requests r WHERE event=? AND ack IS NULL
                        AND NOT EXISTS(SELECT 1 FROM retired_requests t WHERE t.event=r.event AND t.seq=r.seq)""",
                        (event,)).fetchone()
                    if outstanding:continue
                    self._recover_uploads(event,session,self._read(event,session))
                    with self.box.transaction():
                        self.box.db.execute("UPDATE pending_closes SET state='finalized' WHERE event=?",(event,))
                        self.box.recovery_state(event,"reconciled","previous_session_not_resumed")
                    break
                else:raise RecoveryRequired("restart_delivery_pending")
            except Exception as error:
                self.box.recovery_state(event,"required",type(error).__name__)
                raise
        return [row["id"] for row in old]


def dispose_previous_sources(box, *, spool_config, verified_receipt=lambda _:False):
    """Disposition only ledger-owned old spools; never extend their lifetime."""
    from inference_worker.segment_spool import SegmentSpool
    from inference_worker.cleanup_supervisor import CleanupSupervisor
    owners=box.db.execute("SELECT c.* FROM captures c JOIN events e ON e.id=c.event WHERE e.session!=?",
                         (box.session,)).fetchall()
    for owner in owners:
        directory=box.root/owner["spool"]
        box._no_symlinks(directory)
        if not directory.exists():continue
        if (box.recovery_obligations() or box.orphan_cleanup_obligations()):
            raise RecoveryRequired("unmapped_raw_requires_audited_recovery")
        # A registered, unambiguous source can be disposed on interrupted
        # capture failure using the existing evidence_unavailable policy.
        spool=SegmentSpool(directory,**spool_config)
        try:
            for segment in box.db.execute("SELECT * FROM segments WHERE event=? AND camera=?",
                                          (owner["event"],owner["camera"])).fetchall():
                if segment["state"]=="present":
                    box.request_cleanup(segment["id"],reason="restart_incomplete_capture")
            result=CleanupSupervisor(box,spools={owner["spool"]:spool},
                                     verified_receipt=verified_receipt).run_once()
            if spool.entries or spool.active_segment_id or result["recovery_required"]:
                raise RecoveryRequired("old_source_disposition_pending")
            if spool.discard_receipts()["items"]:
                raise RecoveryRequired("old_disposition_receipts_pending")
        finally:spool.close()
        # Explicit owned metadata files only; no recursive or arbitrary deletion.
        for name in ("journal.json","journal.tmp",".lock"):
            (directory/name).unlink(missing_ok=True)
        directory.rmdir()
