"""Local demo runner: lets a slide start a napari script on the presenter's laptop.

    pixi run -e demos runner                        # listens on 127.0.0.1:8765

Only scripts in shared/demos/ can be started, by file name, and only from the
origins listed below. Nothing listens beyond localhost.
"""
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEMOS = Path(__file__).parents[1] / "demos"
ALLOWED_ORIGINS = {"http://localhost:4200", "http://127.0.0.1:4200", "https://timmonko.github.io"}


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)

    def do_OPTIONS(self):
        self.send_response(204); self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST"); self.end_headers()

    def do_GET(self):
        self.send_response(200 if self.path == "/ping" else 404); self._cors(); self.end_headers()

    def do_POST(self):
        name = self.path.removeprefix("/run/")
        script = DEMOS / f"{name}.py"
        if not name.isidentifier() or not script.is_file():
            self.send_response(404); self._cors(); self.end_headers(); return
        subprocess.Popen([sys.executable, str(script)])
        self.send_response(202); self._cors(); self.end_headers()


if __name__ == "__main__":
    print("demo runner on http://127.0.0.1:8765, demos:", sorted(p.stem for p in DEMOS.glob("*.py")))
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
