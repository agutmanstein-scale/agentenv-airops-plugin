from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest

from agentenv_airops.flow import days
from agentenv_airops.programs import candidates

ROOT = Path(__file__).resolve().parents[2]
STEP0 = ROOT / "cache" / "step0" / "full" / "candidates.jsonl"


def dates(first: str, n: int, gap: int) -> list[str]:
    start = date.fromisoformat(first)
    return [(start + timedelta(days=gap * k)).isoformat() for k in range(n)]


def test_quotas_split_ten_per_hub_by_largest_remainder():
    eligible = [(d, "JFK") for d in dates("2023-06-01", 36, 2) + dates("2023-12-01", 10, 2)]
    eligible += [(d, "BOS") for d in dates("2023-06-01", 49, 1) + dates("2023-12-01", 11, 2)]
    assert days.quotas(eligible) == {"JFK": {"summer": 8, "winter": 2}, "BOS": {"summer": 8, "winter": 2}}
    assert days.quotas([(d, "JFK") for d in dates("2023-06-01", 3, 9)] + [(d, "JFK") for d in dates("2024-01-01", 3, 9)]
                       + [(d, "BOS") for d in dates("2023-06-01", 2, 9)])["JFK"] == {"summer": 5, "winter": 5}


def test_the_draw_pools_events_across_hubs_and_train_drops_whole_events():
    jfk = dates("2023-06-01", 15, 6) + dates("2024-01-01", 6, 4) + ["2025-07-01"]
    bos = dates("2023-06-02", 15, 6) + dates("2024-12-01", 3, 4) + ["2025-07-01"]
    built = [(d, "JFK") for d in jfk] + [(d, "BOS") for d in bos]
    prev = ("2024-08-19", "BOS")
    result = days.make(built + [prev], built + [prev], seed=7)
    assert result == days.make(built + [prev], built + [prev], seed=7)
    assert len(result.drawn) == 20 and prev not in result.drawn
    assert not any(days.lost(*k) for k in result.drawn + result.train) and ("2025-07-01", "JFK") in result.excluded
    assert all(n == 1 for n in Counter(result.event[d] for d, _ in result.drawn).values())
    by = Counter((h, candidates.season(d)) for d, h in result.drawn)
    assert by == {(h, s): result.quotas[h][s] for h in ("JFK", "BOS") for s in days.SEASONS}
    held = {result.event[d] for d, _ in result.drawn}
    assert all(result.event[d] not in held for d, _ in result.train)
    assert set(result.train) == {k for k in built + [prev] if result.event[k[0]] not in held and not days.lost(*k)}


def test_a_stratum_without_enough_events_raises():
    built = [(d, "JFK") for d in dates("2023-06-01", 3, 1)] + [(d, "BOS") for d in dates("2023-12-01", 30, 4)]
    with pytest.raises(ValueError, match="events left"):
        days.make(built, built, seed=1)


@pytest.mark.skipif(not STEP0.is_file(), reason="needs the local input cache")
def test_step0s_candidates_give_the_registered_pool_and_quotas():
    keys, built = days.read(STEP0)
    assert (len(keys), len(built)) == (209, 149)
    eligible = [k for k in built if k not in days.PREVIOUS and not days.lost(*k)]
    assert len(eligible) == 106 and sum(days.lost(*k) for k in built) == 24
    assert days.quotas(eligible) == {"JFK": {"summer": 8, "winter": 2}, "BOS": {"summer": 8, "winter": 2}}
