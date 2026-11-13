"""Bounded delivery-only tail after verified source EOS; never extends raw TTL.

The wall budget is checked between existing bounded transport operations, not a
cancellation deadline for an operation already in flight. No receipt is invented.
"""
import time
from inference_worker.cleanup_supervisor import CleanupSupervisor


def delivery_snapshot(runtime):
    box, dispatcher = runtime.outbox, runtime.dispatcher
    count = lambda sql: box.db.execute(sql).fetchone()[0]
    return dict(
        metadata=count('SELECT count(*) FROM requests WHERE ack IS NULL'),
        close_intents=count("SELECT count(*) FROM pending_closes WHERE state!='finalized'"),
        uploads=sum(row['state'] != 'accepted' for row in dispatcher.status()),
        sources=count("SELECT count(*) FROM segments WHERE state='present' OR cleanup=1"),
        recovery=len(box.recovery_obligations()) + len(box.orphan_cleanup_obligations()),
        captures=len(runtime.capture.events),
        # These monotonic counters detect progress even when one pending command
        # is replaced by the next. They are not evidence-completeness claims.
        acknowledged=count('SELECT count(*) FROM requests WHERE ack IS NOT NULL'),
        accepted=sum(row['state'] == 'accepted' for row in dispatcher.status()),
        incomplete=count("SELECT count(*) FROM events WHERE state!='closed'"))


def drain_delivery(runtime, *, max_steps=64, max_seconds=30, clock=time.monotonic):
    """No new frames, source polling, detector calls, or repeated blind uploads."""
    if type(max_steps) is not int or max_steps <= 0 or not 0 < max_seconds <= 30:
        raise ValueError('invalid delivery drain budget')
    pending_keys = ('metadata', 'close_intents', 'uploads', 'sources', 'recovery', 'captures')
    deadline = clock() + max_seconds
    before = delivery_snapshot(runtime)
    for step in range(max_steps):
        if not any(before[key] for key in pending_keys):
            return dict(state='drained', steps=step, remaining=before)
        if clock() >= deadline:
            return dict(state='budget_exhausted', steps=step, remaining=before)
        if runtime.media_watermark is None:
            return dict(state='blocked', steps=step, remaining=before)
        runtime.pump(now=runtime.media_watermark)
        spools = {cap.spool.root.name: cap.spool
                  for event in runtime.capture.events.values()
                  for cap in event['captures'].values() if cap.spool}
        CleanupSupervisor(runtime.outbox, spools=spools,
                          verified_receipt=runtime.dispatcher.cleanup_receipt_verified).run_once()
        runtime.release_finished()
        after = delivery_snapshot(runtime)
        if after == before:
            return dict(state='blocked', steps=step + 1, remaining=after)
        before = after
    state = 'budget_exhausted' if any(before[key] for key in pending_keys) else 'drained'
    return dict(state=state, steps=max_steps, remaining=before)
