"""E2E sem dependência externa: executa a proteção local no Chrome real."""
import contextlib
import base64
import json
import http.server
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def translate_path(self, path):
        if path.split("?", 1)[0] == "/sw.js":
            return str(ROOT / "static" / "sw.js")
        return super().translate_path(path)

    def end_headers(self):
        if self.path.split("?", 1)[0] == "/sw.js":
            self.send_header("Service-Worker-Allowed", "/")
        super().end_headers()

    def log_message(self, *_args):
        return


def chrome_path():
    configured = os.environ.get("CHROME_PATH")
    candidates = [
        configured,
        shutil.which("chrome"),
        shutil.which("google-chrome"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    return next((item for item in candidates if item and Path(item).exists()), None)


class DevToolsSocket:
    def __init__(self, url):
        parsed = urlparse(url)
        self.socket = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        request = (
            f"GET {parsed.path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.socket.sendall(request.encode())
        response = self.socket.recv(4096)
        if b" 101 " not in response:
            raise RuntimeError(f"Falha no WebSocket do Chrome: {response[:100]!r}")
        self.counter = 0

    def _send(self, payload):
        data = payload.encode()
        mask = os.urandom(4)
        length = len(data)
        header = bytearray([0x81])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.socket.sendall(bytes(header) + mask + masked)

    def _receive(self):
        first = self.socket.recv(2)
        if len(first) < 2:
            raise RuntimeError("Chrome encerrou o WebSocket.")
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self.socket.recv(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self.socket.recv(8))[0]
        chunks = bytearray()
        while len(chunks) < length:
            chunks.extend(self.socket.recv(length - len(chunks)))
        return chunks.decode()

    def evaluate(self, expression):
        self.counter += 1
        request_id = self.counter
        self._send(json.dumps({
            "id": request_id,
            "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True},
        }))
        while True:
            message = json.loads(self._receive())
            if message.get("id") == request_id:
                return message.get("result", {}).get("result", {}).get("value")

    def close(self):
        self.socket.close()


class PDVOfflineBrowserE2E(unittest.TestCase):
    def test_indexeddb_backup_reabertura_e_service_worker(self):
        chrome = chrome_path()
        if not chrome:
            self.skipTest("Chrome não encontrado; informe CHROME_PATH.")
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with tempfile.TemporaryDirectory(prefix="pdv-e2e-") as profile:
            process = None
            devtools = None
            try:
                url = f"http://127.0.0.1:{server.server_port}/tests/browser/pdv_offline_harness.html"
                process = subprocess.Popen(
                    [
                        chrome, "--headless=new", "--disable-gpu", "--no-first-run",
                        "--disable-background-networking", f"--user-data-dir={profile}",
                        "--remote-debugging-port=0", "--remote-allow-origins=*", url,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                active_port = Path(profile) / "DevToolsActivePort"
                for _ in range(100):
                    if active_port.exists():
                        break
                    time.sleep(0.05)
                self.assertTrue(active_port.exists(), "Chrome não abriu a porta de depuração.")
                port = active_port.read_text().splitlines()[0]
                for _ in range(100):
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list") as response:
                        targets = json.load(response)
                    target = next((item for item in targets if item.get("type") == "page"), None)
                    if target:
                        break
                    time.sleep(0.05)
                self.assertIsNotNone(target, "Página de teste não apareceu no Chrome.")
                devtools = DevToolsSocket(target["webSocketDebuggerUrl"])
                status = None
                for _ in range(150):
                    status = devtools.evaluate("document.querySelector('#result')?.dataset.status || 'MISSING'")
                    if status in {"PASS", "FAIL"}:
                        break
                    time.sleep(0.1)
                detail = devtools.evaluate("document.querySelector('#result')?.textContent || ''")
                self.assertEqual(status, "PASS", detail)
            finally:
                if devtools:
                    devtools.close()
                if process:
                    process.terminate()
                    with contextlib.suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=5)
                server.shutdown()
                thread.join(timeout=5)
                server.server_close()


if __name__ == "__main__":
    unittest.main()
