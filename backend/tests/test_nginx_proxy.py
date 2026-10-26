"""Real Nginx forwarding regressions; set MONIPAN_NGINX_BINARY to enable.

Uses temporary local listeners only. Production trust addresses are mapped to
loopback in the fixture; this does not verify the production Docker network.
"""

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class EchoHeaders(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(dict(self.headers)).encode()
        self.send_response(401 if self.path == "/api/auth/me" else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


@pytest.fixture(scope="module", params=["local", "production"])
def nginx_proxy(request, tmp_path_factory):
    binary = os.getenv("MONIPAN_NGINX_BINARY") or shutil.which("nginx")
    if not binary:
        pytest.skip("Set MONIPAN_NGINX_BINARY to run real Nginx forwarding tests")
    binary = str(Path(binary).resolve())
    folder = tmp_path_factory.mktemp("nginx-" + request.param)
    (folder / "logs").mkdir()
    (folder / "temp").mkdir()
    (folder / "html").mkdir()
    (folder / "html/index.html").write_text("MoniPan fixture", encoding="utf-8")
    echo = ThreadingHTTPServer(("127.0.0.1", 0), EchoHeaders)
    thread = threading.Thread(target=echo.serve_forever, daemon=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    trust_path = ("frontend/proxy-trust.conf" if request.param == "local"
                  else "deploy/nginx/proxy-trust.production.conf")
    trust = (PROJECT_ROOT / trust_path).read_text(encoding="utf-8")
    trust = trust.replace("172.18.0.1", "127.0.0.1")
    config = (PROJECT_ROOT / "frontend/nginx.conf").read_text(encoding="utf-8")
    config = config.replace("include /etc/nginx/monipan-proxy-trust.conf;", trust)
    config = config.replace("include /etc/nginx/monipan-security-headers.conf;",
                            (PROJECT_ROOT / "frontend/security-headers.conf").read_text(encoding="utf-8"))
    config = config.replace("listen 80;", f"listen 127.0.0.1:{port};")
    config = config.replace("http://backend:8000", f"http://127.0.0.1:{echo.server_port}")
    config = config.replace("root /usr/share/nginx/html;", f'root "{folder.as_posix()}/html";')
    (folder / "nginx.conf").write_text(
        "daemon off;\nmaster_process off;\npid nginx.pid;\nerror_log logs/error.log;\n"
        "events {}\nhttp {\naccess_log off;\n" + config + "\n}\n", encoding="utf-8")
    command = [binary, "-p", folder.as_posix() + "/", "-c", "nginx.conf"]
    validation = subprocess.run(command + ["-t"], capture_output=True, text=True, timeout=15)
    if validation.returncode != 0:
        echo.server_close()
    assert validation.returncode == 0, validation.stderr
    thread.start()
    output = (folder / "process.log").open("wb")
    process = subprocess.Popen(command, stdout=output, stderr=output)
    try:
        for _attempt in range(100):
            if process.poll() is not None:
                pytest.fail((folder / "process.log").read_text(errors="replace"))
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            pytest.fail("Nginx did not start")
        yield request.param, port
    finally:
        subprocess.run(command + ["-s", "quit"], capture_output=True, timeout=10)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        output.close()
        echo.shutdown()
        echo.server_close()
        thread.join(timeout=5)


def fetch(proxy, path="/api/probe", source="127.0.0.1", protocol="https"):
    connection = http.client.HTTPConnection("127.0.0.1", proxy[1], timeout=5,
                                            source_address=(source, 0))
    try:
        connection.request("GET", path, headers={
            "X-Real-IP": "203.0.113.10", "X-Forwarded-For": "198.51.100.99",
            "X-Forwarded-Proto": protocol,
        })
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("path", ["/api/probe", "/api/auth/login", "/api/auth/register",
                                   "/api/agent/stocks/600000/analyze"])
def test_api_routes_preserve_only_trusted_forwarding(nginx_proxy, path):
    status, _headers, body = fetch(nginx_proxy, path)
    assert status == 200
    forwarded = json.loads(body)
    trusted = nginx_proxy[0] == "production"
    assert forwarded["X-Forwarded-Proto"] == ("https" if trusted else "http")
    assert forwarded["X-Real-IP"] == ("203.0.113.10" if trusted else "127.0.0.1")
    assert forwarded["X-Forwarded-For"] == forwarded["X-Real-IP"]


def test_untrusted_peer_cannot_spoof_ip_or_protocol(nginx_proxy):
    status, _headers, body = fetch(nginx_proxy, source="127.0.0.2")
    assert status == 200
    forwarded = json.loads(body)
    assert forwarded["X-Real-IP"] == "127.0.0.2"
    assert forwarded["X-Forwarded-For"] == "127.0.0.2"
    assert forwarded["X-Forwarded-Proto"] == "http"


def test_invalid_forwarded_protocol_falls_back_to_connection_protocol(nginx_proxy):
    status, _headers, body = fetch(nginx_proxy, protocol="https,http")
    assert status == 200
    assert json.loads(body)["X-Forwarded-Proto"] == "http"


@pytest.mark.parametrize("path,status", [("/", 200), ("/api/auth/me", 401), ("/docs", 404)])
def test_enforcing_security_headers_cover_success_and_errors(nginx_proxy, path, status):
    actual, headers, _body = fetch(nginx_proxy, path)
    assert actual == status
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert "Content-Security-Policy-Report-Only" not in headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "Strict-Transport-Security" not in headers  # Host TLS layer owns HSTS.


def test_rate_limit_response_keeps_security_headers(nginx_proxy):
    for _attempt in range(10):
        status, headers, _body = fetch(nginx_proxy, "/api/auth/login", source="127.0.0.3")
        if status == 429:
            assert headers["Retry-After"] == "60"
            assert "Content-Security-Policy" in headers
            assert "Content-Security-Policy-Report-Only" not in headers
            break
    else:
        pytest.fail("Login edge limit did not reject the bounded request burst")
