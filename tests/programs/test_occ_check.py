import importlib.util

from program_helpers import ROOT

from agentenv_airops.programs import occ

SPEC = importlib.util.spec_from_file_location("occ_check", ROOT / "scripts" / "occ_check.py")
occ_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(occ_check)


def desk_row(date: str, desk: float = 120.0, as_run: bool = True, split: str = "train") -> dict:
    return {"date": date, "task_id": f"b6-{date}", "split": split, "desk_replay": {"units_as_run": as_run},
            "bands": {"middle": {"joint": {"played": {"crew_desk": {"flat": desk, "notice": desk}},
                                           "flat": {"best": 100.0, "bound": None, "from": "notice plan"},
                                           "notice": {"best": 100.0, "bound": 90.0}}}}}


def broken(date: str) -> dict:
    return {"date": date, "task_id": f"b6-{date}", "split": "train", "error": "Traceback", "seconds": 1.0,
            "peak_rss_gb": 0.1}


def mode(notice: float) -> dict:
    return {"notice": notice, "flat": notice, "F2": {"ok": True}, "F3": {"ok": True}, "F8": [], "F4": [],
            "cancelled": dict.fromkeys((occ.RULE, occ.CUT, occ.REQUEST, occ.FLAG), 0), "desk": 0, "auto": 0,
            "sent": {"CANCEL": 0, "HOLD": 0}, "accepted": {"CANCEL": 0, "HOLD": 0}, "crew_cost": {"end": 0.0},
            "seconds": 1.0}


def done_row(date: str, talk: float, failed: tuple = ()) -> dict:
    modes = {m: mode({"quiet": 110.0, "quiet-flags": 112.0, "talk": talk}.get(m, 115.0)) for m in occ.MODES}
    return {"date": date, "task_id": f"b6-{date}", "split": "train", "F1": {"ok": not failed, "failed": list(failed)},
            "F7": {"same": True, "flights": [], "notice": 0.0, "seconds": 1.0}, "modes": modes,
            "bar2": occ_check.gain(120.0, 90.0, 100.0, {m: v["notice"] for m, v in modes.items()}),
            "checks_ok": not failed, "B0": 120.0, "laps": {"setup": 1.0}, "seconds": 2.0, "peak_rss_gb": 0.5}


def test_sixty_percent_of_the_eval_dates_restates_twelve_of_twenty():
    assert [occ_check.need(n) for n in (20, 22, 24)] == [12, 14, 15]


def test_bar_one_fails_an_error_a_desk_replay_off_the_run_units_and_a_missing_date_and_says_why():
    rows = {"d1": desk_row("d1"), "d2": desk_row("d2", as_run=False), "d3": broken("d3"),
            "d4": desk_row("d4", desk=105.0), "c1": desk_row("c1", split="control")}
    split = {"eval": {d: "JFK" for d in ("d1", "d2", "d3", "d4", "d5")}, "train": [], "neither": []}
    b = occ_check.bar1(rows, split, None)
    assert (b["gate"]["n"], b["gate"]["need"], b["gate"]["passed"], b["gate"]["pass"]) == (5, 3, 1, False)
    assert b["failed"] == {"d2": ["units_as_run false"], "d3": ["error in the desk replay"], "d4": ["room under 10%"],
                           "d5": ["missing"]}
    assert (b["eval"]["d1"]["room"], b["eval"]["d1"]["upper"]) == (0.2, round(120 / 90 - 1, 4))
    assert list(b["controls"]) == ["c1"] and b["storm"]["errors"] == 1


def test_bar_two_reads_the_better_no_channel_script_against_the_floored_bound_and_fails_a_date_its_checks_fail():
    rows = {d: desk_row(d) for d in ("d1", "d4")} | {"d2": desk_row("d2", as_run=False), "d3": broken("d3")}
    split = {"eval": {d: "BOS" for d in rows}, "train": [], "neither": []}
    done = {"d1": done_row("d1", 108.0), "d2": done_row("d2", 108.0, ("units_as_run",)), "d3": broken("d3"),
            "d4": done_row("d4", 109.0)}
    g = occ_check.gates(rows, done, split)
    assert g["bar2"]["eval"]["d1"]["gain"] == round(100 * (110 - 108) / max(120 - 90, 0.1 * 90), 4)
    assert g["bar2"]["failed"] == {"d2": ["F1: units_as_run"], "d3": ["error"], "d4": ["gain under 5"]}
    assert g["bar1"]["failed"] == {"d2": ["units_as_run false", "checks"], "d3": ["error in the desk replay"]}
    assert (g["bar2"]["gate"]["passed"], g["bar1"]["gate"]["passed"], g["go"]) == (1, 2, False)
    assert g["checks"]["errors"] == ["d3"] and g["checks"]["failing"] == {"d2": ["F1"]}
    report = occ_check.markdown({**g, "commit": "abc"}, rows, done, split)
    assert "d2 (F1: units_as_run)" in report and "d2 (units_as_run false, checks)" in report
