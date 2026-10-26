"""Managed website entry point. No permanent polling or background service."""
import logging
import os
from app import config, database
from app.wsgi import application, initialise


def main():
    from waitress import serve
    database.validate_runtime()
    initialise()
    host = os.getenv('IP', os.getenv('HOST', '127.0.0.1'))
    port = int(os.getenv('PORT', '8000'))
    if not 1 <= port <= 65535:
        raise ValueError('Неверный порт веб-сервера.')
    # Account memory is 256 MiB. Limit input buffers and prevent plaintext spills.
    maximum = config.request_body_limit()
    logging.basicConfig(level=logging.WARNING)
    serve(application, host=host, port=port, threads=4, connection_limit=16,
          channel_timeout=60, cleanup_interval=10, max_request_header_size=32768,
          max_request_body_size=maximum, inbuf_overflow=maximum + 1,
          outbuf_overflow=2**60, expose_tracebacks=False, log_socket_errors=False)


if __name__ == '__main__':
    main()
