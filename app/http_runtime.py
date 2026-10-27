"""Bounded production transport; proxy identity remains in WSGIHandler.remote."""
from . import config


def settings():
    maximum = config.request_body_limit()
    return dict(threads=4, connection_limit=16, backlog=32,
                channel_timeout=60, cleanup_interval=10,
                max_request_header_size=32768, max_request_body_size=maximum,
                # Keep decrypted request/response data out of temporary files.
                inbuf_overflow=maximum + 1, outbuf_overflow=2**60,
                expose_tracebacks=False, log_socket_errors=False,
                clear_untrusted_proxy_headers=True,
                channel_request_lookahead=0)


def serve(host, port):
    from waitress import serve as waitress_serve
    from .wsgi import application
    # No trusted_proxy override: REMOTE_ADDR must remain the socket peer.
    # The app trusts X-Orbita-Client-IP only from Caddy over loopback.
    waitress_serve(application, host=host, port=port, **settings())
