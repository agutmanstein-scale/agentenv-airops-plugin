import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location("occ_thumbs", ROOT / "scripts" / "occ_thumbs.py")
th = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(th)


def entry(date, occ="two desks", run="occ-board-sol", featured=None):
    return {"url": f"{run}-{occ[:3]}.{date}.json", "date": date, "occ": occ, "run": run, "featured": featured}


def test_the_list_order_and_its_views_put_the_featured_first_and_a_room_card_every_fifth():
    v1 = {"url": "v1.json", "date": "2023-07-01"}
    pilot, late, early = entry("2023-06-01", run="occ-pilot-sol"), entry("2024-01-02"), entry("2023-07-08", "one agent")
    star = entry("2025-01-11", featured=1)
    days = [entry(f"2023-08-{d:02d}") for d in range(10, 16)]
    order = th.listed([v1, pilot, late, early, star, *days])
    assert order[:4] == [star, early, *days[:2]] and order[-2:] == [late, pilot]
    wants = th.plan(order)
    assert [wants[e["url"]] for e in order[:7]] == ["best", "gate", "tower", "push", "gate", "room", "gate"]


def cand(kind, score, t=600, key="N1", aircraft=1, near=0.3):
    return {"kind": kind, "score": score, "t": t, "key": key, "aircraft": aircraft, "near": near}


def test_a_card_takes_its_view_unless_it_is_much_weaker_never_a_still_without_aircraft_or_its_days_other_one():
    tower, gate, push, empty = cand("tower", 3.0), cand("gate", 2.8, key="N2"), cand("push", 3.4, 650), \
        cand("gate", 3.9, aircraft=0)
    assert th.choose([tower, gate, push, empty], "gate") is gate
    assert th.choose([tower, cand("gate", 2.0), push], "gate") is push
    assert th.choose([tower, gate, push, empty], "best") is push
    big = cand("gate", 3.2, key="N3", near=0.6)
    assert th.choose([tower, gate, push, big], "best") is big
    assert th.choose([tower, gate, push], "push", other=("push", "N1", 650)) is tower
    assert th.choose([cand("tower", 3.3, near=0.25), push], "tower")["kind"] == "tower"
    assert th.choose([cand("tower", 3.3, near=0.14), push], "tower") is push
    assert th.choose([empty], "gate") is None
    room = cand("room", 0.9, aircraft=0)
    assert th.choose([tower, room], "room") is room and th.choose([tower], "room") is tower


def test_fog_a_blank_wall_the_dark_glare_or_a_far_or_hidden_aircraft_score_below_a_detailed_daylight_frame():
    flat = Image.new("RGB", (640, 360), (150, 155, 160))
    busy = Image.new("RGB", (640, 360), (120, 130, 140))
    draw = ImageDraw.Draw(busy)
    for x in range(0, 640, 24):
        draw.rectangle((x, 120 + (x % 96), x + 10, 300), fill=(230, 232, 235))
        draw.line((x, 0, x + 40, 360), fill=(40, 45, 50), width=3)
    dark = busy.point(lambda v: v // 6)
    seen = {"aircraft": [{"jetblue": True, "hidden": False, "shown": 1.0, "width": 0.4}], "sun": 30}
    scores = [th.score(seen, im)["score"] for im in (busy, flat, dark)]
    assert scores[0] > scores[1] and scores[0] > scores[2]
    for plane in ({"hidden": True, "width": 0.4}, {"hidden": False, "width": 0.08}):
        seen = {"aircraft": [{"jetblue": True, "shown": 1.0} | plane], "sun": 30}
        assert th.score(seen, busy) == {"score": 0.0, "aircraft": 0}
    glare = busy.copy()
    ImageDraw.Draw(glare).ellipse((380, 10, 560, 140), fill=(255, 255, 255))
    seen = {"aircraft": [{"jetblue": True, "hidden": False, "shown": 1.0, "width": 0.4}], "sun": 30}
    assert th.score(seen, glare)["score"] < th.score(seen, busy)["score"]
