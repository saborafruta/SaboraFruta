"""Healthcheck HTTP mínimo para serviços Celery no Railway."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = 200 if self.path.rstrip('/') == '/health' else 404
        payload = json.dumps({'status': 'ok' if status == 200 else 'not_found'}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


if __name__ == '__main__':
    server = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8080'))), HealthHandler)
    server.serve_forever()
