"""Compatibility entry point; the test runner lives in scripts.test."""
from scripts.test import main

if __name__ == '__main__':
    raise SystemExit(main())
