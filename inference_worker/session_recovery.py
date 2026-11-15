"""Original-session recovery; never restore tracks, rewrite bytes, or claim media ready."""
import hashlib
import json
import re
from pathlib import Path
from inference_worker.continuous_payloads import _time


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

    def _flush(self):
        for _ in range(self.max_steps):
            result=self.dispatcher.metadata_once(max_items=1)
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

    def _reports(self,event,session,remote):
        binding=self.box.db.execute("SELECT metadata FROM runtime_bindings WHERE session=?",(session,)).fetchone()
        if binding is None:raise RecoveryRequired("original_clock_binding_missing")
        provenance=json.loads(binding[0])
        if provenance["endpoint"].rstrip("/")!=self.endpoint:
            raise RecoveryRequired("original_endpoint_mismatch")
        for camera in remote["cameras"]:
            name=camera["camera_id"];old=camera.get("capture_report")
            segments=[s for s in remote["segments"] if s["camera_id"]==name]
            if old and old["state"]=="incomplete":continue
            if old and old["state"]=="complete" and segments and all(s["status"]=="received" for s in segments):
                continue  # Existing server custody is never downgraded for local cleanup.
            pending=self.box.db.execute("SELECT 1 FROM requests WHERE event=? AND idem LIKE ?",
                                       (event,name+":"+event+":restart-report%")).fetchone()
            if pending:continue
            clock=provenance["clocks"][name]
            sequence=(old["sequence"] if old else 0)+1
            missing=list(old.get("missing_ranges",[])) if old else []
            gap={"start_at":camera["required_start_at"],"end_at":camera["required_end_at"],
                 "reason":"process_restart_evidence_unavailable"}
            if gap not in missing:missing.append(gap)
            body=dict(session_id=session,sequence=sequence,state="incomplete",
                captured_ranges=old.get("captured_ranges",[]) if old else [],missing_ranges=missing,
                earliest_available_at=old.get("earliest_available_at") if old else None,
                time_basis=old.get("time_basis","source_utc_corrected") if old else "source_utc_corrected",
                clock_offset_seconds=old.get("clock_offset_seconds",clock["offset"]) if old else clock["offset"],
                clock_uncertainty_seconds=old.get("clock_uncertainty_seconds",clock["error_bound"]) if old else clock["error_bound"],
                reason="process_restart_evidence_unavailable")
            self.box.enqueue(event,seq=self.box.event_status(event)["next_seq"],kind="capture_report",
                idempotency_key=f"{name}:{event}:restart-report:{sequence}",body=body)

    def run(self):
        """Bounded recovery passes. Unknown/protected state blocks new frames."""
        old=self.box.db.execute("SELECT * FROM events WHERE session!=?",(self.box.session,)).fetchall()
        for eventrow in old:
            event,session=eventrow["id"],eventrow["session"]
            # Fully acknowledged finalized sessions need no new event mutation.
            known=self.box.db.execute("SELECT state FROM recovery_states WHERE event=?",(event,)).fetchone()
            if known and known[0]=="reconciled":continue
            try:
                for _ in range(3):
                    self._terminal_ambiguities(event,session)
                    self._flush()
                    remote=self._read(event,session)
                    self._incomplete_open(event,session,remote)
                    self._flush()
                    remote=self._read(event,session)
                    if remote["lifecycle"]["phase"]=="open":continue
                    self._reports(event,session,remote)
                    self._flush()
                    outstanding=self.box.db.execute("""SELECT 1 FROM requests r WHERE event=? AND ack IS NULL
                        AND NOT EXISTS(SELECT 1 FROM retired_requests t WHERE t.event=r.event AND t.seq=r.seq)""",
                        (event,)).fetchone()
                    if outstanding:continue
                    with self.box.transaction():
                        self.box.db.execute("UPDATE pending_closes SET state='finalized' WHERE event=?",(event,))
                        self.box.recovery_state(event,"reconciled","previous_session_not_resumed")
                    break
                else:raise RecoveryRequired("restart_delivery_pending")
            except Exception as error:
                self.box.recovery_state(event,"required",type(error).__name__)
                raise
        return [row["id"] for row in old]


def dispose_previous_sources(box, *, spool_config):
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
                                     verified_receipt=lambda _:False).run_once()
            if spool.entries or spool.active_segment_id or result["recovery_required"]:
                raise RecoveryRequired("old_source_disposition_pending")
            if spool.discard_receipts()["items"]:
                raise RecoveryRequired("old_disposition_receipts_pending")
        finally:spool.close()
        # Explicit owned metadata files only; no recursive or arbitrary deletion.
        for name in ("journal.json","journal.tmp",".lock"):
            (directory/name).unlink(missing_ok=True)
        directory.rmdir()
