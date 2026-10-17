"""The playable day's clock (docs/v3/DESIGN.md §4, §5): the watches, the news each one delivers, and virtual time.

The watches are the follower's (`references.watch_times`), so a desk playing the day and the follower decide at the
same minutes; `watch_times` is the one place that list comes from, for the event-driven watches of §4 to replace. The
news is the hubs' FAA advisories as the task carries them (`task.programs[hub].advisories`), each reaching the desk at
the first watch at or after its signature: those signed before the first watch come with the opening briefing, those
after the last never arrive. A tool call costs CALL_MIN minutes of the watch's virtual time, so thinking costs time
without tying the score to the provider's speed; `virtual_time` is that minute as the UTC instant a gateway clock
reads (v1's schedule.virtual_time)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from ..core.model import hhmm
from ..programs import references
from ..programs.advisories import GDP, GDP_CNX, GS, GS_CNX, PROPOSED, Advisory
from ..programs.model import Task

CALL_MIN = 1
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
KINDS = {GDP: "ground delay program", PROPOSED: "proposed ground delay program", GDP_CNX: "GDP cancelled",
         GS: "ground stop", GS_CNX: "ground stop cancelled"}


@dataclass(frozen=True)
class News:
    """One advisory as the desk reads it: `at` its signature."""

    id: str
    hub: str
    kind: str
    at: int
    text: str

    def to_dict(self) -> dict:
        return {"id": self.id, "hub": self.hub, "kind": self.kind, "at": hhmm(self.at), "text": self.text}


@dataclass(frozen=True)
class Watch:
    index: int
    t: int
    news: tuple[str, ...]


def watch_times(task: Task) -> list[int]:
    return references.watch_times(task)


def news(task: Task) -> list[News]:
    """Every hub's advisories in signature order."""
    advs = sorted((a for p in task.programs.values() for a in p.advisories), key=lambda a: (a.issued, a.id))
    return [News(a.id, a.hub, a.kind, a.issued, text(a)) for a in advs]


def timetable(task: Task) -> list[Watch]:
    times = watch_times(task)
    due: dict[int, list[str]] = {k: [] for k in range(len(times))}
    for n in news(task):
        k = next((k for k, t in enumerate(times) if t >= n.at), None)
        if k is not None:
            due[k].append(n.id)
    return [Watch(k, t, tuple(due[k])) for k, t in enumerate(times)]


def text(a: Advisory) -> str:
    line = f"{a.hub} {KINDS.get(a.kind, a.kind)}"
    if a.start is not None:
        line += f" {hhmm(a.start)}" + (f"-{hhmm(a.end)}" if a.end is not None else "")
    if a.rates:
        line += f", rate {'/'.join(map(str, a.rates))} arrivals/h (all carriers)"
    if a.avg_delay is not None:
        line += f", average delay {a.avg_delay} min" + (f", max {a.max_delay}" if a.max_delay is not None else "")
    if a.extension:
        line += f", probability of extension {a.extension.lower()}"
    if a.only:
        line += f" (only {a.only})"
    return line + f" [advisory {a.id}, signed {hhmm(a.issued)}]"


def virtual_time(task: Task, minute: int) -> str:
    """The minute of the task's day, hub-local, as the UTC instant the gateway clock reads."""
    midnight = datetime.fromisoformat(task.date).replace(tzinfo=ZoneInfo(task.tz))
    return (midnight + timedelta(minutes=minute)).astimezone(UTC).strftime(TIME_FORMAT)
