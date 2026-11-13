"""Opt-in single-owner cleanup bridge. No automatic scheduling or public output.

Caller supplies already-open, exclusively owned SegmentSpools. Never open an
unreconciled spool here: startup may discard unregistered raw files. Orphans and
aggregate inventory failures remain manual recovery, not guessed deletion.
"""
from pathlib import Path
import stat


class CleanupSupervisor:
    def __init__(self, outbox, *, spools, verified_receipt):
        if not callable(verified_receipt):
            raise ValueError('verified receipt lookup required')
        self.outbox = outbox
        self.spools = dict(spools)  # UUID directory -> already-owned SegmentSpool
        self.verified_receipt = verified_receipt

    def run_once(self):
        result = dict(cleaned=[], held=[], recovery_required=False)
        # Audit state is committed before any destructive operation below.
        # Failure stops the whole pass rather than silently deleting unaudited data.
        self.outbox.reconcile()
        recovery = self.outbox.recovery_obligations()
        orphans = self.outbox.orphan_cleanup_obligations()
        blocked = {row['spool'] for row in recovery}
        blocked.update(row['path'].split('/')[0] for row in orphans)
        result['recovery_required'] = bool(blocked)
        for row in self.outbox.cleanup_obligations():
            sid = row['id']
            directory = row['path'].split('/')[0]
            held = lambda reason: result['held'].append(dict(segment_id=sid, reason=reason))
            if directory in blocked:
                held('manual_orphan_reconciliation'); continue
            spool = self.spools.get(directory)
            if spool is None:
                held('spool_not_owned'); continue
            try:
                expected_root = self.outbox.root / directory
                self.outbox._no_symlinks(expected_root)
                if Path(spool.root).absolute() != expected_root:
                    held('spool_owner_path_mismatch'); continue
                path = self.outbox._path(row['path'])
                if path.exists():
                    info = path.stat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != row['bytes']:
                        held('source_identity_requires_manual_recovery'); continue
                receipts = spool.discard_receipts()
                if receipts['reconcile_required']:
                    result['recovery_required'] = True
                    held('spool_disposition_overflow'); continue
                delivered = row['state'] == 'uploaded_private' and row['reason'] == 'verified_upload_receipt'
                if delivered:
                    try:
                        verified = self.verified_receipt(dict(row)) is True
                    except Exception:
                        verified = False
                    if not verified:
                        now = self.outbox.clock()
                        if now < row['created'] or now-row['created'] >= self.outbox.ttl:
                            self.outbox.request_cleanup(sid, reason='source_expired_or_clock_rollback')
                            row = self.outbox.segment_status(sid)
                            delivered = False
                        else:
                            held('private_custody_not_verified'); continue
                if not delivered and row['state'] != 'unavailable':
                    held('no_durable_disposition'); continue
                owned = spool.entries.get(sid)
                if owned is not None and (owned.byte_count != row['bytes'] or owned.created_at != row['created']
                        or owned.capture_start != row['start'] or owned.capture_end != row['end']):
                    held('segment_manifest_mismatch'); continue
                if owned is None and path.exists():
                    held('unregistered_source_manual_recovery'); continue
                if delivered:
                    spool.acknowledge(sid)
                elif owned is not None:
                    # An unavailable source never becomes delivered. Its original
                    # detailed reason remains in the event/segment durable ledger.
                    reason = ('privacy_ttl_expired' if row['reason']=='source_expired_or_clock_rollback'
                              else 'evidence_unavailable')
                    spool.discard(sid, reason=reason)
                # Keep cleanup intent pending until receipt drain commits too.
                # A crash/retry after raw deletion safely repeats absence checks.
                # Ledger disposition survives pruning bounded spool receipts.
                ids = [item['receipt_sequence'] for item in spool.discard_receipts()['items']
                       if item['segment_id'] == sid]
                if ids:
                    spool.acknowledge_discards(ids)
                self.outbox.acknowledge_cleanup(sid)
                result['cleaned'].append(dict(segment_id=sid,
                    outcome='verified_private_delivery' if delivered else 'incomplete_privacy_or_failure_discard'))
            except Exception as exc:
                # Intent remains durable, so next pass/restart retries safely.
                held('retry_or_manual_recovery:' + type(exc).__name__)
        result['receipts_joined'] = []
        for directory, spool in self.spools.items():
            if directory in blocked:
                continue
            try:
                expected_root = self.outbox.root / directory
                self.outbox._no_symlinks(expected_root)
                if Path(spool.root).absolute() != expected_root:
                    raise ValueError('spool owner mismatch')
                receipts = spool.discard_receipts()
                if receipts['reconcile_required']:
                    result['recovery_required'] = True
                    raise ValueError('discard overflow requires manual reconciliation')
                for receipt in receipts['items']:
                    if self.outbox.record_unbound_discard(directory, receipt):
                        # The owner/camera/receipt audit and incomplete state are
                        # committed before releasing the bounded spool receipt.
                        spool.acknowledge_discards([receipt['receipt_sequence']])
                        result['receipts_joined'].append(receipt['segment_id'])
            except Exception as exc:
                result['held'].append(dict(spool=directory,reason='receipt_reconciliation:'+type(exc).__name__))
        return result
