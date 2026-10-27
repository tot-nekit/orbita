"""Historical reminder fixtures: pin only the validation clock, never disable the guard."""
from unittest.mock import patch
from app import service

_original = service.validate


def historical(now='2026-01-01T00:00:00+00:00'):
    """Fixtures dated before today are valid as of `now`; explicit clocks still win."""
    def validate(data, current=None, now_value=None):
        return _original(data, current, now_value=now_value or now)
    return patch.object(service, 'validate', validate)
