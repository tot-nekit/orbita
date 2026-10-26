"""Request/task identity must come from authentication, never from JSON input."""
from contextvars import ContextVar
from contextlib import contextmanager
OWNER=ContextVar('orbita_owner',default='web')
@contextmanager
def scope(owner):
    token=OWNER.set(owner)
    try:yield
    finally:OWNER.reset(token)
def owner():return OWNER.get()
