import json
from dataclasses import replace

from day_helpers import closed, closed_day, day, flight, outcapped, tail

from agentenv_airops.programs import references, sim
from agentenv_airops.programs.model import LAGS, Task
from agentenv_airops.v3day import clock
from agentenv_airops.v3day import tasks as v3_tasks
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


def test_news_lags_carry_it_past_a_watch_and_the_tasks_own_lags_move_its_delivery_and_its_watch():
    task = replace(stormy(), layers=replace(stormy().layers, weather=WEATHER + ((58, "metar", "JFK", "KJFK 260558Z"),)))
    when = delivered(task)
    assert when["metar-jfk-58"] == 120 and next(t for t in clock.watch_times(task) if t >= 58) == 60
    slow = replace(task, layers=replace(task.layers, lags=LAGS | {"metar": 15, "speci": 20}))
    assert 170 in references.events(slow) and 155 not in references.events(slow)
    when = delivered(slow)
    assert when["speci-jfk-150"] == 170 and when["metar-jfk-50"] == 120


def delivered(task: Task) -> dict[str, int]:
    d = Day(task)
    while not d.done:
        d.advance()
    return {e["news_id"]: e["t"] for e in d.log}


def test_two_weather_items_of_one_kind_hub_and_minute_each_reach_the_desk_once():
    same = ((50, "metar", "JFK", "KJFK 260550Z 18012KT 10SM RMK AO2"),
            (50, "metar", "JFK", "KJFK 260550Z AUTO 18012KT 10SM RMK MADISHF"))
    task = replace(closed_day(), layers=replace(closed_day().layers, weather=same))
    ids = [n.id for n in clock.news(task)]
    assert len(set(ids)) == 2
    d = Day(task)
    d.advance()
    texts = [d.items[i].text for i in d.delivered]
    assert len(d.delivered) == 2 and sorted(texts) == sorted(" ".join(x[3].split()) for x in same)


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
    assert s["closures"] == {"JFK": [{"from": "02:00", "to": "03:05", "reported": False, "dep_cap": 3,
                                      "arr_cap": 1}]}
    assert "+TSRA" in s["weather"]["JFK"]["observation"]["text"] and s["weather"]["JFK"]["observation"]["due"]
    text = d.text()
    assert "## Outstation arrival caps" in text and "BUF: 03h 1 06h 1" in text
    assert "JFK 02:00-03:05 (end expected)" in text and "+TSRA" in text
    assert s["weather"]["JFK"]["taf"]["text"].startswith("TAF AMD KJFK 260720Z")


def test_a_day_without_layers_shows_no_caps_closures_or_weather():
    task = replace(closed_day(), layers=None)
    s = Day(task).situation()
    assert s["arr_caps"] == s["closures"] == s["weather"] == {} and s["news"] == []
    text = Day(task).text()
    assert "## Ramp closures (lightning at the field; known once begun)\nNone." in text
    assert "known 240 min ahead\nNone known." in text and "## Latest weather (METAR or SPECI, and TAF)\nNone." in text


def test_a_closure_ending_on_a_watch_is_reported_over_at_it():
    d = Day(closed(day([flight(0, "JFK", "BUF", 400, 60)], [tail("N1", "JFK", end="BUF")]),
                   {"JFK": ((120, 180, 1, 2),)}, {"JFK": 0.5}))
    while d.t < 180:
        d.advance()
    assert d.t == 180 and d.situation()["closures"]["JFK"] == [
        {"from": "02:00", "to": "03:00", "reported": True, "dep_cap": 1, "arr_cap": 2}]


def test_a_closure_is_reported_over_only_once_its_end_has_come_even_when_the_guess_matches_it():
    task = closed(day([flight(0, "JFK", "BUF", 400, 60)], [tail("N1", "JFK", end="BUF")]),
                  {"JFK": ((120, 120 + sim.GUESS, 1, 1),)}, {"JFK": 0.5})
    d = Day(task)
    while d.t < 120:
        d.advance()
    assert d.situation()["closures"]["JFK"][0]["reported"] is False
    d.advance()
    assert d.t == 180 and d.situation()["closures"]["JFK"] == [
        {"from": "02:00", "to": "02:30", "reported": True, "dep_cap": 1, "arr_cap": 1}]


def test_the_prompt_on_a_layered_day_tells_of_event_watches_lags_outstation_caps_closures_and_weather():
    rules, plain = v3_tasks.rules(stormy()), v3_tasks.rules(replace(stormy(), layers=None))
    assert "in watches, one an hour, starting at watch 0" in plain and "one an hour" in rules
    assert "Outstation arrival caps" not in plain and "SPECI" not in plain and "Ramp closures" not in plain
    for words in ["15 to 60 minutes apart", "a METAR 5 and a SPECI 5 minutes after its observation",
                  "a TAF 10 minutes after its issue", "- Outstation arrival caps:", "- Ramp closures:",
                  f"expected to last {sim.GUESS} minutes more", "the latest METAR or SPECI and TAF per hub"]:
        assert words in rules
    slow = replace(stormy(), layers=replace(stormy().layers, lags=LAGS | {"taf": 25}))
    assert "a TAF 25 minutes after its issue" in v3_tasks.rules(slow)
