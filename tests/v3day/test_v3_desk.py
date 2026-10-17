import json
from dataclasses import replace

from day_helpers import closed_day, outcapped

from agentenv_airops.programs import references
from agentenv_airops.programs.model import LAGS, Task
from agentenv_airops.v3day import clock
from agentenv_airops.v3day.day import Day
from agentenv_airops.v3day.situation import KEYS

WEATHER = ((-100, "taf", "JFK", "TAF\nKJFK 260420Z 2605/2706 18010KT P6SM"),
           (-9, "metar", "JFK", "KJFK 260451Z 18010KT 10SM"),
           (50, "metar", "JFK", "KJFK 260551Z 18012KT 10SM"),
           (150, "speci", "JFK", "KJFK 260730Z 27020G35KT 1SM +TSRA"),
           (140, "taf", "JFK", "TAF AMD\nKJFK 260720Z 2608/2712 27015KT 3SM TSRA"))


def stormy() -> Task:
    """closed_day (JFK's ramp closed 120-180) with JFK's weather, and BUF capping hours 3 and 6 at one arrival."""
    task = outcapped(closed_day(), {"BUF": {3: 1, 6: 1}})
    return replace(task, layers=replace(task.layers, weather=WEATHER))


def test_weather_reaches_the_desk_at_the_first_watch_after_its_minute_plus_its_lag():
    task = stormy()
    d = Day(task)
    assert [w.t for w in d.watches] == references.watch_times(task) == clock.watch_times(task)
    due = {n.id: n.due for n in clock.news(task)}
    assert due == {f"{k}-jfk-{m}": m + LAGS[k] for m, k, _, _ in WEATHER}
    while not d.done:
        d.advance()
    when = {e["news_id"]: e["t"] for e in d.log}
    times = [w.t for w in d.watches]
    assert set(when) == set(due)
    for n, t in when.items():
        assert t == next(x for x in times if x >= due[n])
    assert times == [0, 60, 120, 155] and when["speci-jfk-150"] == 155 and when["taf-jfk-140"] == 155
    assert when["metar-jfk-50"] == 60 and when["taf-jfk--100"] == when["metar-jfk--9"] == 0


def test_the_situation_shows_outstation_caps_closures_and_the_latest_weather():
    d = Day(stormy())
    s = d.situation()
    assert set(KEYS) <= set(s) and json.loads(json.dumps(s)) == s
    assert s["arr_caps"] == {"BUF": {"03": 1}} and s["closures"] == {}
    assert s["weather"]["JFK"]["observation"]["text"].startswith("KJFK 260451Z")
    assert s["weather"]["JFK"]["taf"]["text"].startswith("TAF KJFK 260420Z")
    while d.t < 155:
        d.advance()
    s = d.situation()
    assert s["arr_caps"] == {"BUF": {"03": 1, "06": 1}}
    assert s["closures"] == {"JFK": [{"from": "02:00", "to": "03:05", "reported": False, "dep_cap": 1,
                                      "arr_cap": 2}]}
    assert "+TSRA" in s["weather"]["JFK"]["observation"]["text"] and s["weather"]["JFK"]["observation"]["due"]
    text = d.text()
    assert "## Outstation arrival caps" in text and "BUF: 03h 1 06h 1" in text
    assert "JFK 02:00-03:05 (end expected)" in text and "+TSRA" in text
    assert s["weather"]["JFK"]["taf"]["text"].startswith("TAF AMD KJFK 260720Z")


def test_a_day_without_layers_shows_no_caps_closures_or_weather():
    task = replace(closed_day(), layers=None)
    s = Day(task).situation()
    assert s["arr_caps"] == s["closures"] == s["weather"] == {} and s["news"] == []
    assert "None under way" not in Day(task).text() and "## Ramp closures" in Day(task).text()
