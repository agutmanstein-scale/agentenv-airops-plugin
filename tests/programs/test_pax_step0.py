import importlib.util
import json

from program_helpers import ROOT

SPEC = importlib.util.spec_from_file_location("pax_step0", ROOT / "scripts" / "pax_step0.py")
pax_step0 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pax_step0)


def row(date: str, k: float, combined: float, gap: float, wall: float = 1700.0) -> dict:
    shares = {"plan": combined / 2, "rebooking": combined / 2, "combined": combined}
    return {"date": date, "k": k, "split": "eval", "shares": shares, "G2": {"proven_1pct": gap <= 1 and wall <= 1800},
            "changed": {"either_changed_share": 0.3}, "mip": {"gap_pct": gap, "wall_s": wall}}


def test_the_guards_are_recomputed_from_the_per_date_files_alone(tmp_path):
    days = tmp_path / "dates"
    days.mkdir()
    for n in range(24):
        date = f"2024-07-{n + 1:02d}"
        rows = [row(date, 1.0, 0.06 if n < 12 else 0.04, 0.5 if n < 20 else 2.0), row(date, 0.5, 0.01, 0.1)]
        for r in rows:
            pax_step0.path(days, date, r["k"]).write_text(json.dumps(r))
    g = pax_step0.guards(tmp_path)
    assert (g["G1"]["passing"], g["G1"]["pass"]) == (12, True)
    assert (g["G2"]["passing"], g["G2"]["pass"]) == (20, True)
    assert g["medians"]["0.5"]["combined"] == 0.01
    pax_step0.path(days, "2024-07-01", 1.0).write_text(json.dumps(row("2024-07-01", 1.0, 0.04, 0.5)))
    assert not pax_step0.guards(tmp_path)["G1"]["pass"]
    assert "G1: FAIL" in pax_step0.report(pax_step0.guards(tmp_path))
