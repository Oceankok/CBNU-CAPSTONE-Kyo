"""Cooperative GPU yielding; inference integration supplies the busy signal."""
from contextlib import contextmanager
from contextvars import ContextVar

_before_gpu = ContextVar('before_gpu', default=None)


def yield_to_inference():
    callback = _before_gpu.get()
    if callback:
        callback()


@contextmanager
def inference_guard(callback):
    token = _before_gpu.set(callback)
    try:
        yield
    finally:
        _before_gpu.reset(token)


def busy_until(conn):
    """Aggregate legacy node signals and per-execution signals from active processors."""
    row = conn.execute("""SELECT MAX(l.busy_until) FROM (
        SELECT node_id,busy_until FROM processing_inference_load
        UNION ALL SELECT node_id,busy_until FROM processing_inference_session
        ) l JOIN processing_worker w ON w.node_id=l.node_id AND w.enabled=1
        JOIN field_node n ON n.node_id=l.node_id AND n.is_active=1""").fetchone()
    return row[0] or 0
