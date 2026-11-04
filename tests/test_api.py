import http.client
import json
import socket

import pytest

from clickforge_core import ApiServer


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class App:
    def __init__(self):
        self.enabled = False

    def toggle(self):
        self.enabled = not self.enabled

    def state(self):
        return {"enabled": self.enabled, "multiplier": 12}


@pytest.fixture
def server():
    app = App()
    api = ApiServer({"/toggle": app.toggle}, app.state, lambda fn: fn(), port=free_port())
    yield app, api.httpd.server_address[1]
    api.stop()


def request(port, method, path, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, headers=headers or {})
    response = conn.getresponse()
    body = json.loads(response.read())
    conn.close()
    return response.status, body


def test_state(server):
    _, port = server
    assert request(port, "GET", "/state") == (200, {"enabled": False, "multiplier": 12})


def test_toggle_needs_the_custom_header(server):
    app, port = server
    assert request(port, "POST", "/toggle")[0] == 404
    assert app.enabled is False
    status, body = request(port, "POST", "/toggle", {"X-ClickForge": "1"})
    assert status == 200 and body["enabled"] is True


def test_unknown_routes_are_404(server):
    _, port = server
    assert request(port, "GET", "/nope")[0] == 404
    assert request(port, "POST", "/nope", {"X-ClickForge": "1"})[0] == 404

