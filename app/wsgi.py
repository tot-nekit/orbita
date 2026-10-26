"""WSGI transport for the existing API; authentication and ownership stay shared."""
import io
import ipaddress
import os
import threading
from email.message import Message
from http import HTTPStatus
from urllib.parse import quote
from . import store, security
from .server import Handler

_init_lock = threading.Lock()
_initialised_path = None


def initialise():
    global _initialised_path
    with _init_lock:
        if _initialised_path != str(store.DB_PATH):
            store.init()
            security.secret()
            _initialised_path = str(store.DB_PATH)


class WSGIHandler(Handler):
    """Adapt only HTTP I/O. Never emulate a socket or parse a second HTTP request."""
    def __init__(self, environ):
        self.path = quote(environ.get('PATH_INFO', '/'), safe='/')
        if environ.get('QUERY_STRING'):
            self.path += '?' + environ['QUERY_STRING']
        self.headers = Message()
        for key, value in environ.items():
            if key.startswith('HTTP_'):
                self.headers[key[5:].replace('_', '-')] = value
        for key, label in [('CONTENT_TYPE', 'Content-Type'), ('CONTENT_LENGTH', 'Content-Length')]:
            if environ.get(key):
                self.headers[label] = environ[key]
        self.client_address = (environ.get('REMOTE_ADDR', ''), 0)
        self.rfile = environ['wsgi.input']
        self.wfile = io.BytesIO()
        self.status_code = 200
        self.response_headers = []

    def remote(self):
        if os.getenv('HOSTING_PROFILE') != 'alwaysdata':return super().remote()
        # The platform overwrites X-Real-IP. Trust it only from an explicitly
        # configured proxy network; never accept the VPS-specific header here.
        address = self.client_address[0]
        try:
            peer = ipaddress.ip_address(address)
            networks = os.getenv('TRUSTED_PROXY_CIDRS', '').split(',')
            if any(value.strip() and peer in ipaddress.ip_network(value.strip()) for value in networks):
                return str(ipaddress.ip_address(self.headers.get('X-Real-IP', '')))
        except ValueError:pass
        return address

    def send_response(self, code, message=None):
        self.status_code = code

    def send_header(self, name, value):
        self.response_headers.append((name, value))

    def end_headers(self):
        pass


def application(environ, start_response):
    initialise()
    request = WSGIHandler(environ)
    method = environ.get('REQUEST_METHOD', 'GET')
    if method not in ('GET', 'POST'):
        request.output({'error': 'Метод не поддерживается.'}, 405, headers={'Allow': 'GET, POST'})
    else:
        request.dispatch(method)
    code = request.status_code
    start_response(str(code) + ' ' + HTTPStatus(code).phrase, request.response_headers)
    return [request.wfile.getvalue()]
