import io

import numpy as np
import pytest
from flow_helpers import obs
from PIL import Image

from agentenv_airops.flow import costs, sources, weather
from agentenv_airops.flow.weather import Bin


def row(valid: str, drct: str, sknt: str, vsby: str, sky: tuple, wx: str, metar: str) -> dict:
    layers = list(sky) + [("", "")] * (4 - len(sky))
    values = {"station": "JFK", "valid": valid, "drct": drct, "sknt": sknt, "vsby": vsby, "gust": "", "wxcodes": wx,
              "metar": metar}
    for i, (c, h) in enumerate(layers, 1):
        values[f"skyc{i}"], values[f"skyl{i}"] = c, h
    return values


def test_an_observation_reads_wind_ceiling_and_the_storm_and_snow_flags():
    o = weather.observation(row("2024-07-12 22:51", "250", "12", "2.5", [("FEW", "4000"), ("BKN", "2500")], "+TSRA",
                                "KJFK 122251Z 25012KT 2 1/2SM +TSRA FEW040CB BKN025 RMK AO2 TSB30"), 0)
    assert (o.wind_dir, o.wind_kt, o.ceiling_ft, o.vis_sm, o.ts, o.vicinity, o.frozen) == (250, 12, 2500, 2.5, True,
                                                                                            False, False)
    near = weather.observation(row("2024-07-12 23:51", "", "0", "10", [("FEW", "5000")], "VCSH",
                                   "KJFK 122351Z 00000KT 10SM FEW050CB RMK AO2 LTG DSNT W"), 0)
    assert near.vicinity and not near.ts and near.ceiling_ft is None
    quiet = weather.observation(row("2024-07-13 00:51", "10", "5", "10", [], "BLSN",
                                    "KJFK 130051Z 01005KT 10SM CLR RMK AO2 TSNO"), 0)
    assert not (quiet.vicinity or quiet.frozen or quiet.ts)
    snow = weather.observation(row("2024-01-13 00:51", "10", "5", "1", [("OVC", "800")], "-FZRA BR", "KJFK ..."), 0)
    assert snow.frozen


def test_a_bin_takes_the_observation_in_force_and_a_storm_intensity():
    seq = (obs(at=-9), obs(at=51, ts=True), obs(at=58))
    assert weather.in_force(seq, 7) == seq[0] and weather.in_force(seq, 52) == seq[1] and weather.in_force(seq, -50)
    assert Bin(obs(ts=True), 0.0).storm == 1 and Bin(obs(), 0.03).storm == pytest.approx(0.3)
    assert Bin(obs(), 0.5).storm == 1 and Bin(obs(), None).storm == 0


def test_radar_shares_count_the_disc_at_each_threshold():
    masks = sources.discs()
    rows, cols, mask = masks["JFK"]
    idx = np.zeros((rows.stop + 5, cols.stop + 5), dtype=np.uint8)
    box = idx[rows, cols]
    box[mask] = 145
    box[: box.shape[0] // 2][mask[: box.shape[0] // 2]] = 125
    im = Image.fromarray(idx, "P")
    im.putpalette([0, 0, 0] * 256)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    got = sources.shares(buf.getvalue(), {"JFK": masks["JFK"]})["JFK"]
    assert got["20"] == 1.0 and got["30"] == 1.0 and 0.4 < got["40"] < 0.6 and got["50"] == 0.0


def test_counted_arrivals_read_the_atr_and_massport_tables():
    assert sources.counted_per_day("JFK", "2024-07") == pytest.approx(42_627 / 2 / 31)
    assert sources.counted_per_day("JFK", "2023-07") == pytest.approx(42_627 / 1.038 / 2 / 31)
    assert sources.counted_per_day("JFK", "2022-12") == pytest.approx(39_115 / 0.979 / 2 / 31)
    assert sources.counted_per_day("BOS", "2025-07") == pytest.approx(19_031 / 31)
    with pytest.raises(ValueError):
        sources.counted_per_day("JFK", "2025-07")


def test_table_4_7_rates_and_the_model_classes():
    assert costs.RATES["narrowbody 165k lb or more"] == costs.Rates(34, 95)
    assert costs.RATES["widebody 580k lb or more"] == costs.Rates(64, 219)
    assert costs.RATES["regional jet 61-99 seats"] == costs.Rates(20, 56)
    cases = {("A320-232", 150): "narrowbody 165k lb or more", ("A321-271NX", 196): "narrowbody 165k lb or more",
             ("737-7H4", 143): "narrowbody under 165k lb", ("737-823", 172): "narrowbody 165k lb or more",
             ("BD-500-1A11", 140): "narrowbody under 165k lb", ("E190-100", 100): "narrowbody under 165k lb",
             ("ERJ170-200LR", 76): "regional jet 61-99 seats", ("EMB-145XR", 50): "regional jet under 61 seats",
             ("CL-600-2D24", 76): "regional jet 61-99 seats", ("777-222", 360): "widebody 580k lb or more",
             ("A330-243", 290): "widebody under 580k lb", ("DHC-8-402", 76): "turboprop over 60 seats",
             ("208B", 9): "turboprop under 20 seats", ("A319-112", 128): "narrowbody under 165k lb"}
    assert {k: costs.classify(*k) for k in cases} == cases
    assert costs.classify("XYZ", 10) is None
    assert costs.weighted_median([(3, 1), (1, 1), (2, 5)]) == 2
