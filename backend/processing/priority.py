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
