import http.client
import json
import queue
import socket
import threading
import time

import pytest

from clickforge_core import ApiServer, run_pending


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


def test_a_busy_port_means_no_api_rather_than_a_crash(server):
    app, port = server
    second = ApiServer({}, app.state, lambda fn: fn(), port=port)
    assert second.httpd is None  # SO_REUSEADDR let it bind, and the two shared the port
    second.stop()


def test_an_action_that_raises_still_answers_quickly():
    app = App()
    pending = queue.Queue()

    def broken():
        raise ValueError("bad state")

    api = ApiServer({"/toggle": broken}, app.state, pending.put, port=free_port())
    port = api.httpd.server_address[1]
    worker = threading.Thread(target=lambda: (time.sleep(0.05), run_pending(pending)))
    worker.start()
    started = time.monotonic()
    status, _ = request(port, "POST", "/toggle", {"X-ClickForge": "1"})
    elapsed = time.monotonic() - started
    worker.join()
    api.stop()
    assert status == 200
    assert elapsed < 0.5  # used to sit out the whole wait


def test_a_stuck_ui_still_answers_before_the_plugin_gives_up():
    app = App()
    api = ApiServer({"/toggle": app.toggle}, app.state, lambda job: None, port=free_port())
    started = time.monotonic()
    status, _ = request(api.httpd.server_address[1], "POST", "/toggle", {"X-ClickForge": "1"})
    elapsed = time.monotonic() - started
    api.stop()
    assert status == 200
    assert elapsed < 0.8  # past the plugin's timeout it took the app for closed and started another


def test_a_rebound_domain_is_refused(server):
    app, port = server
    evil = {"X-ClickForge": "1", "Host": f"evil.example:{port}"}
    assert request(port, "POST", "/toggle", evil)[0] == 403  # used to switch the clicker on
    assert app.enabled is False
    assert request(port, "GET", "/state", {"Host": "evil.example"})[0] == 403


def test_the_names_the_plugin_uses_are_fine(server):
    _, port = server
    assert request(port, "GET", "/state", {"Host": f"localhost:{port}"})[0] == 200
    assert request(port, "GET", "/state", {"Host": f"127.0.0.1:{port}"})[0] == 200


def test_stop_frees_the_port():
    app = App()
    port = free_port()
    first = ApiServer({}, app.state, lambda fn: fn(), port=port)  # kept, so the GC can't close it
    first.stop()
    again = ApiServer({}, app.state, lambda fn: fn(), port=port)
    assert again.httpd is not None
    again.stop()
