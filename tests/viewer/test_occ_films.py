import fcntl
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("occ_films", ROOT / "scripts" / "occ_films.py")
of = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(of)


def sweep(tmp: Path, name: str, runs: list[str], rows: list[dict], tail: str = "") -> Path:
    folder = tmp / "runs" / name
    (folder / "days").mkdir(parents=True)
    (folder / "pilot.json").write_text(json.dumps({"name": name, "model": "m", "runs": runs, "pack": None}))
    for r in rows:
        if r.get("day"):
            (folder / r["day"]).write_text("{}")
    (folder / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows) + tail)
    return folder


def add(folder: Path, r: dict) -> None:
    (folder / r["day"]).write_text("{}")
    with (folder / "results.jsonl").open("a") as out:
        out.write(json.dumps(r) + "\n")


def row(run: str, attempt: int = 1, outcome: str = "scored") -> dict:
    return {"run": run, "attempt": attempt, "outcome": outcome,
            "day": f"days/{run}-a{attempt}.json" if outcome == "scored" else None}


class Stub(of.Films):
    def __init__(self, *a, fail: str | None = None, **kw):
        super().__init__(*a, **kw)
        self.fail, self.calls = fail, []

    def build(self, r, log):
        self.calls.append(("build", r["run"]))
        index = self.viewer / "episodes" / "index.json"
        doc = json.loads(index.read_text()) if index.is_file() else {"episodes": []}
        doc["episodes"].append({"task_id": r["run"].rsplit("-", 1)[0], "run": self.label(r["run"])})
        index.write_text(json.dumps(doc))
        (self.viewer / "episodes" / f"stem-{r['run']}.json").write_text("{}")
        return f"stem-{r['run']}"

    def shoot(self, stem, run, board, poster, log):
        self.calls.append(("film", run, board, poster))
        if self.fail == run:
            raise RuntimeError("record.py failed twice")
        (self.dir / f"{run}.mp4").write_bytes(b"mp4")

    def thumb(self, stem, run, log):
        (self.dir / f"{run}.jpg").write_bytes(b"jpg")


class Unbuilt(Stub):
    def build(self, r, log):
        if r["run"] == "b-joint":
            raise ValueError("no world")
        return super().build(r, log)


def films(tmp: Path, folder: Path, kind: type = Stub, **kw) -> Stub:
    f = kind(folder, tmp / "films", tmp, tmp, True, False, [], **kw)
    f.dir.mkdir(parents=True, exist_ok=True)
    f.prepare()
    return f


def film_all(f: Stub, final: bool = False, retry: bool = False) -> list[str]:
    todo, _ = f.todo([], final, retry)
    ready = [x for x in map(f.prepared, todo) if x]
    board = f.boarded()
    for x in ready:
        f.film(*x, board)
    return [r["run"] for r in todo]


def test_a_sweep_in_play_films_each_scored_run_once_and_a_half_written_row_waits(tmp_path):
    folder = sweep(tmp_path, "occ3-pilot-sol", ["a-joint", "a-one", "b-joint"],
                   [row("a-one"), row("b-joint", outcome="failed")], tail='{"run": "a-joint", "atte')
    f = films(tmp_path, folder)
    assert film_all(f) == ["a-one"]
    done = json.loads((f.dir / "done" / "a-one.done.json").read_text())
    assert done["attempt"] == 1 and done["film"] == "a-one.mp4" and done["board"] is None
    assert f.todo([], False, False) == ([], True)
    (folder / "results.jsonl").write_text(json.dumps(row("a-one")) + "\n" + json.dumps(row("b-joint", outcome="failed"))
                                          + "\n")
    add(folder, row("a-joint"))
    add(folder, row("b-joint", 2))
    assert film_all(f) == ["a-joint", "b-joint"]
    assert f.todo([], False, False) == ([], False)
    assert [c[1] for c in f.calls if c[0] == "build"] == ["a-one", "a-joint", "b-joint"]


def test_a_board_ends_on_its_means_only_once_final_and_final_films_again_the_ones_before(tmp_path):
    folder = sweep(tmp_path, "occ3-board-sol", ["a-joint", "b-joint"], [row("a-joint")])
    f = films(tmp_path, folder)
    film_all(f)
    assert f.calls[-1] == ("film", "a-joint", False, True)
    add(folder, row("b-joint"))
    assert film_all(f) == ["b-joint"] and f.calls[-1] == ("film", "b-joint", True, True)
    assert film_all(f) == []
    assert film_all(f, final=True) == ["a-joint"] and f.calls[-1] == ("film", "a-joint", True, False)
    assert film_all(f, final=True) == [] and [c[1] for c in f.calls if c[0] == "build"] == ["a-joint", "b-joint"]


def test_a_final_board_missing_a_scored_day_from_the_folder_ends_without_its_means(tmp_path):
    folder = sweep(tmp_path, "occ3-board-sol", ["a-joint", "b-joint"], [row("a-joint"), row("b-joint")])
    f = films(tmp_path, folder, Unbuilt)
    assert film_all(f) == ["a-joint", "b-joint"] and f.calls[-1] == ("film", "a-joint", False, True)
    assert json.loads((f.dir / "done" / "b-joint.failed.json").read_text())["stage"] == "build"


def test_a_failed_film_is_left_until_retry_and_leaves_no_film(tmp_path):
    folder = sweep(tmp_path, "occ3-pilot-sol", ["a-one"], [row("a-one")])
    f = films(tmp_path, folder, fail="a-one")
    assert film_all(f) == ["a-one"]
    failed = json.loads((f.dir / "done" / "a-one.failed.json").read_text())
    assert failed["stage"] == "film" and "record.py" in failed["error"]
    assert not (f.dir / "a-one.mp4").exists() and film_all(f) == []
    f.fail = None
    assert film_all(f, retry=True) == ["a-one"]
    assert (f.dir / "done" / "a-one.done.json").is_file() and not (f.dir / "done" / "a-one.failed.json").exists()


def test_a_second_run_on_a_folder_being_filmed_exits(tmp_path, capsys):
    folder = sweep(tmp_path, "occ3-pilot-sol", ["a-one"], [row("a-one")])
    out = tmp_path / "films" / folder.name
    out.mkdir(parents=True)
    with (out / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert of.main([str(folder), "--out", str(tmp_path / "films")]) == 0
    assert "another run" in capsys.readouterr().out
    assert not (out / "done").exists()


def test_polling_a_final_sweep_retries_a_failure_once_and_ends(tmp_path, monkeypatch):
    folder = sweep(tmp_path, "occ3-pilot-sol", ["a-one", "b-one"], [row("a-one"), row("b-one")])
    tries = []
    monkeypatch.setattr(of.Films, "build", Stub.build)
    monkeypatch.setattr(of.Films, "thumb", Stub.thumb)

    def shoot(self, stem, run, board, poster, log):
        tries.append(run)
        if run == "b-one":
            raise RuntimeError("record.py failed twice")
        (self.dir / f"{run}.mp4").write_bytes(b"mp4")

    monkeypatch.setattr(of.Films, "shoot", shoot)
    monkeypatch.setattr(of.Films, "calls", [], raising=False)
    args = [str(folder), "--out", str(tmp_path / "films"), "--every", "0"]
    assert of.main(args) == 0 and tries == ["a-one", "b-one"]
    assert of.main([*args, "--retry"]) == 0 and tries == ["a-one", "b-one", "b-one"]


def test_a_scored_row_whose_day_has_not_landed_waits_and_so_does_a_sweep_without_its_manifest(tmp_path):
    folder = sweep(tmp_path, "occ3-board-sol", ["a-joint"], [row("a-joint")])
    (folder / "days" / "a-joint-a1.json").unlink()
    f = films(tmp_path, folder)
    assert f.todo([], False, False) == ([], False)
    add(folder, row("a-joint"))
    assert [r["run"] for r in f.todo([], False, False)[0]] == ["a-joint"]
    assert of.main([str(tmp_path / "runs" / "occ3-board-x"), "--out", str(tmp_path / "films")]) == 1
