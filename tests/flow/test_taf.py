from datetime import UTC, datetime

import pytest
from flow_helpers import CACHE, needs_cache

from agentenv_airops.build import bts, pack
from agentenv_airops.build import weather as v1
from agentenv_airops.flow import taf

BODY = """TAF
KJFK 311120Z 3112/0118 18012G24KT P6SM SCT008 BKN012 BKN040
      TEMPO 3112/3114 5SM -SHRA OVC010
     FM311500 19012KT P6SM SCT015 BKN025
     FM311900 19015KT 1 1/2SM -SHRA SCT035 BKN050 PROB30 3119/3123 4SM -TSRA BKN025CB
     FM010200 24007KT P6SM VCTS BKN080
     FM010900 VRB03KT P6SM SKC="""
ISSUED = datetime(2024, 7, 31, 11, 20, tzinfo=UTC)
BASE = bts.epoch_minutes(datetime(2024, 7, 31, 4, 0, tzinfo=UTC))


def test_a_taf_reads_its_validity_groups_and_elements():
    t = taf.parse(BODY, ISSUED, BASE)
    assert (t.issued, t.start, t.end) == (440, 480, 2280)
    assert t.base == {"wind": (180.0, 12.0, 24.0), "vis": 6.0, "sky": (1200, False)}
    kinds = [(g.kind, g.start, g.end) for g in t.groups]
    assert kinds == [("TEMPO", 480, 600), ("FM", 660, None), ("FM", 900, None), ("PROB", 900, 1140),
                     ("FM", 1320, None), ("FM", 1740, None)]
    assert t.groups[2].elements["vis"] == 1.5 and t.groups[3].elements["sky"] == (2500, True)
    assert taf.parse("TAF\nKJFK NIL=", ISSUED, BASE) is None


def test_prevailing_and_alternatives_through_the_day_and_past_validity():
    t = taf.parse(BODY, ISSUED, BASE)
    now, alts = taf.state(t, 500)
    assert now.ceiling_ft == 1200 and now.gust_kt == 24.0 and alts[0].ceiling_ft == 1000 and alts[0].vis_sm == 5.0
    now, alts = taf.state(t, 1000)
    assert now.vis_sm == 1.5 and not now.ts and alts[0].ts and alts[0].vis_sm == 4.0 and alts[0].wind_kt == 15.0
    now, alts = taf.state(t, 1400)
    assert now.vicinity and not now.ts and now.ceiling_ft == 8000 and alts == []
    now, alts = taf.state(t, 5000)
    assert now.wind_dir is None and now.ceiling_ft is None and alts == []
    assert taf.in_force([t], 439) is None and taf.in_force([t], 440) is t


@needs_cache
def test_every_cached_taf_parses_with_a_base_wind_and_visibility():
    for hub in ("JFK", "BOS"):
        for month in pack.MONTHS:
            products = v1.products(v1.afos_path(CACHE, f"TAF{hub}", month).read_text(encoding="latin-1"), month)
            assert products
            for issued, body in products:
                t = taf.parse(body, issued, 0)
                assert t is not None and "wind" in t.base and "vis" in t.base, body[:80]


@pytest.mark.parametrize("token,vis", [("P6SM", 6.0), ("3/4SM", 0.75), ("2SM", 2.0)])
def test_visibility_tokens(token, vis):
    assert taf.elements([token])["vis"] == vis
