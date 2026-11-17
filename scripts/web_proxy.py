#!/usr/bin/env python3
"""Local authenticated proxy for Cloud Shell Web Preview.

Forwards incoming requests on localhost:8080 to the deployed Cloud Run service,
automatically injecting an authenticated Google OIDC Identity Token.
This allows seamless use of Cloud Shell's 'Web Preview' without 401 or 403 errors.
"""

import http.server
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

PORT = int(os.environ.get("PROXY_PORT", "8080"))
SERVICE_NAME = os.environ.get("AGENT_SERVICE_NAME", "dinnerguest-agent")
REGION = os.environ.get("REGION", "us-central1")

_CACHED_TOKEN = None
_TOKEN_EXPIRY = 0


def get_service_url() -> str:
    url = os.environ.get("SERVICE_URL")
    if url:
        return url.rstrip("/")
    cmd = [
        "gcloud", "run", "services", "describe", SERVICE_NAME,
        f"--region={REGION}", "--format=value(status.url)"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return res.stdout.strip().rstrip("/")


def get_identity_token(target_url: str) -> str:
    global _CACHED_TOKEN, _TOKEN_EXPIRY
    now = time.time()
    if _CACHED_TOKEN and now < _TOKEN_EXPIRY:
        return _CACHED_TOKEN
    cmd = ["gcloud", "auth", "print-identity-token", f"--audiences={target_url}"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    _CACHED_TOKEN = res.stdout.strip()
    _TOKEN_EXPIRY = now + 300  # cache 5 mins
    return _CACHED_TOKEN


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    target_url = ""

    def do_GET(self) -> None:
        self._proxy("GET")

    def do_POST(self) -> None:
        self._proxy("POST")

    def do_PUT(self) -> None:
        self._proxy("PUT")

    def do_DELETE(self) -> None:
        self._proxy("DELETE")

    def do_HEAD(self) -> None:
        self._proxy("HEAD")

    def _proxy(self, method: str) -> None:
        dest_url = f"{self.target_url}{self.path}"
        try:
            token = get_identity_token(self.target_url)
        except Exception as exc:
            self.send_error(500, f"Failed to acquire Cloud Run ID token: {exc}")
            return

        body = None
        content_len = self.headers.get("Content-Length")
        if content_len:
            body = self.rfile.read(int(content_len))

        req = urllib.request.Request(dest_url, data=body, method=method)
        for header, val in self.headers.items():
            if header.lower() not in ("host", "authorization", "cookie"):
                req.add_header(header, val)
        req.add_header("Authorization", f"Bearer {token}")

        try:
            with urllib.request.urlopen(req) as resp:
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in ("transfer-encoding", "content-length"):
                        self.send_header(k, v)
                data = resp.read()
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as err:
            self.send_response(err.code)
            for k, v in err.headers.items():
                if k.lower() not in ("transfer-encoding", "content-length"):
                    self.send_header(k, v)
            err_data = err.read()
            self.send_header("Content-Length", str(len(err_data)))
            self.end_headers()
            self.wfile.write(err_data)
        except Exception as err:
            self.send_error(502, f"Proxy Error connecting to Cloud Run backend: {err}")

    def log_message(self, format: str, *args) -> None:
        sys.stderr.write(f"[WebProxy] {self.address_string()} - {format % args}\n")


def main() -> None:
    target_url = get_service_url()
    ProxyHandler.target_url = target_url
    print(f"==========================================================")
    print(f" Dinnerguest Cloud Shell Web Preview Proxy")
    print(f" Proxying: http://127.0.0.1:{PORT} -> {target_url}")
    print(f" Click 'Web Preview' in Cloud Shell and select Port {PORT}!")
    print(f"==========================================================")
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), ProxyHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
