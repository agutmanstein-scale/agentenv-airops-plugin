"""`agent-env airops record`: film a recorded run on the viewer with the checkout's scripts/record.py (headless Chrome
and ffmpeg): the run is replayed and exported under the checkout's cache/episodes, beside the radar frames, hub twins
and 3D airport stages it points at, and filmed from there."""

import subprocess
import sys
from pathlib import Path

import click

from . import export
from .episodes import Runs, require
from .sweep import RUNS

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "record.py"


@click.command("record", context_settings={"ignore_unknown_options": True})
@click.argument("sweep")
@click.option("--task", "task_id", required=True, help="The day to film.")
@click.option("--model", help="The run's model. Default: the sweep's only one.")
@click.option("--rep", type=click.IntRange(min=1), default=1, show_default=True, help="The rep to film.")
@click.argument("record_args", nargs=-1, type=click.UNPROCESSED)
def record_command(sweep: str, task_id: str, model: str | None, rep: int, record_args: tuple[str, ...]):
    """Film the scored run of SWEEP on a day; the rest of the arguments go to scripts/record.py, e.g.
    --director --out cache/films/NAME.mp4 [--webp cache/films/NAME.webp] [--seconds 66] for a cut of the day on the
    3D airport, --shots cache/shots for stills or --thumb cache/thumbs/NAME.jpg for the episode list. Needs a checkout
    of the plugin with the viewer extra, Chrome and ffmpeg."""
    if not SCRIPT.is_file():
        raise click.UsageError(f"filming needs a checkout of the plugin: {SCRIPT} doesn't exist")
    runs = require(Runs(RUNS, [sweep]))
    models = sorted({r.row["model"] for r in runs.runs})
    if model is None and len(models) > 1:
        raise click.UsageError(f"the sweep played {', '.join(models)}: pass --model")
    found = [r for r in runs.runs if (r.row["task_id"], r.row["rep"]) == (task_id, rep)
             and r.row["model"] == (model or models[0])]
    if not found:
        raise click.UsageError(f"no scored run of {task_id} rep {rep} in {sweep}")
    cache = ROOT / "cache"
    ep = Runs(RUNS, [sweep], cache).episode(found[0])
    export.write(cache, [ep], cache)
    path = cache / "episodes" / export.file_name(ep)
    click.echo(f"Wrote {path}")
    raise SystemExit(subprocess.run([sys.executable, str(SCRIPT), "--episode", str(path), *record_args]).returncode)
