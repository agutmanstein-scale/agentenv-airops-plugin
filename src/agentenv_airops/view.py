"""`agent-env airops view`: recorded sweeps replayed and served read-only on the AirOpsEnv viewer: the viewer at /web/,
the episodes, their traffic and their index at /cache/episodes/, where it looks by default, and the airport stages,
radar frames and hub twins they point at under /cache/."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

import click

from . import export
from .episodes import Runs, require
from .sweep import RUNS

TYPES = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".json": "application/json",
         ".geojson": "application/geo+json", ".gz": "application/gzip", ".png": "image/png", ".jpg": "image/jpeg",
         ".webp": "image/webp", ".svg": "image/svg+xml", ".mp4": "video/mp4"}
NOT_FOUND = (404, {"Content-Type": "text/plain"}, b"not found")

Response = tuple[int, dict[str, str], bytes]


def route(files: dict[str, bytes], path: str) -> Response:
    """``files`` is the viewer folder export.folder builds, by path."""
    url = urlsplit(path)
    parts = [unquote(p) for p in url.path.split("/") if p]
    if url.path in ("/", "/web", "/web/"):
        return 302, {"Location": "/web/index.html"}, b""
    match parts:
        case ["web", *rest]:
            return _file(export.WEB, "/".join(rest))
        case ["cache", *rest] if "/".join(rest) in files:
            name = "/".join(rest)
            return 200, {"Content-Type": TYPES.get(Path(name).suffix, "application/octet-stream")}, files[name]
    return NOT_FOUND


def _file(root: Path, name: str) -> Response:
    root = root.resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return NOT_FOUND
    return 200, {"Content-Type": TYPES.get(path.suffix, "application/octet-stream")}, path.read_bytes()


def serve(files: dict[str, bytes], port: int, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, headers, body = route(files, self.path)
            self.send_response(status)
            for key, value in (headers | {"Cache-Control": "no-cache", "Content-Length": str(len(body))}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    return ThreadingHTTPServer((host, port), Handler)


@click.command("view")
@click.argument("sweeps", nargs=-1)
@click.option("--port", default=8238, show_default=True, help="Port to serve on.")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Address to serve on: 0.0.0.0 for every interface, as in a container.")
@click.option("--cache", type=click.Path(file_okay=False, path_type=Path), default=export.CACHE, show_default=True,
              help="Where scripts/build_stage.py, prebake_traffic.py, prebake_radar.py and build_twin.py left the "
                   "airport stages, the traffic, the radar frames and the hub twins.")
def view_command(sweeps: tuple[str, ...], port: int, host: str, cache: Path):
    """Replay recorded sweeps (default: all under results/runs) on the AirOpsEnv viewer: the airport in 3D and the US
    map on the virtual clock, each watch's news and changes in plain English, the score and the departures board, the
    agent's tool calls in expert mode, and the day flown on the truth."""
    runs = require(Runs(RUNS, list(sweeps) or None, cache))
    episodes = runs.episodes()
    for f in runs.failed:
        click.echo(f"not replayed: {f['run']} {f['model']} {f['task_id']}: {f['error']}", err=True)
    try:
        server = serve(export.folder(episodes, cache), port, host)
    except OSError as e:
        raise click.UsageError(f"can't serve on port {port} ({e.strerror}): pass another --port") from e
    click.echo(f"Serving {len(episodes)} episodes at http://{host}:{server.server_port}/web/ (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
