"""v3's task prompts and bundle steps, in tasks.py's style (v1's prompts stay as they are): the rules of a storm date
on the programs world as the system prompt, the first watch as the opening prompt, and the steps that play one task
live on airops-v3-day with airops-llm and grade it with airops-verifier."""

from ..core.known import CAP_LOOK, FREEZE, LOOK, STEP
from ..core.model import hhmm
from ..programs import references, sim
from ..programs.cost import NOTICE
from ..programs.model import Task
from ..schedule import AGENT, EXTRA_TURNS, TURNS_PER_WATCH, VERIFIER
from .clock import CALL_MIN, virtual_time
from .day import Day
from .live import END_DAY_URI, ENV, LIVE_LOAD_URI

LOAD_SLACK = 120  # s past the anchor's time limit for a load that solves it: reading the day and building the MIP

def rules(task: Task) -> str:
    """The system prompt: v1's desk on the programs world, with the crew paragraph when the task has crews, and on a
    layered task (docs/v3/DESIGN.md §4) its event-driven watches, the weather news and its delivery lags, the
    outstation arrival caps and the ramp closures."""
    layered = task.layered
    watches = (f"in watches starting at watch 0: one an hour, and one more when an advisory or a SPECI reaches "
               f"the desk or a ramp closure begins, so watches are {references.WATCH_MIN} to {STEP} minutes "
               "apart" if layered else "in watches, one an hour, starting at watch 0")
    news = (f"FAA Command Center advisories and the hubs' METARs, SPECIs and TAFs arrive as messages at the first "
            f"watch after they reach the desk: an advisory {task.lag('advisory')} minutes after it is signed, a METAR "
            f"{task.lag('metar')} and a SPECI {task.lag('speci')} minutes after its observation, a TAF "
            f"{task.lag('taf')} minutes after its issue" if layered
            else "FAA Command Center advisories arrive as messages as they are signed")
    layers = (f"\n- Outstation arrival caps: an outstation may take only so many JetBlue arrivals in an hour (the "
              f"outstation arrival caps table, known {CAP_LOOK // 60} hours ahead); a flight with no room in its "
              "arrival hour leaves so as to land in the next one.\n- Ramp closures: lightning at the field cuts a "
              "hub's ramp rate without stopping it. During a closure only so many JetBlue flights may leave the hub "
              "and only so many arrive there; a departure with no room leaves at the closure's end, an arrival with "
              "none leaves so as to land at it. A closure is known once it begins; until its end comes it is expected "
              f"to last {sim.GUESS} minutes more, with caps for that long, and the closures table says which ends "
              "are reported and which expected." if layered else "")
    situation = (", the outstation arrival caps known so far, the ramp closures under way or over, the latest METAR "
                 "or SPECI and TAF per hub" if layered else "")
    crew = ("\n- Crews: a flight's crew has a deadline, the latest it may leave under its duty limit; a flight not "
            "gone by its crew deadline may leave only once its crew's legal rest ends (still by its deadline), and one "
            "whose rest ends past its deadline is cancelled automatically at the deadline. The table shows each "
            "flight's crew deadline and rest end." if any(f.crew for f in task.flights) else "")
    return f"""You run the JetBlue operations desk on a storm day at JFK and BOS: thunderstorms, snow or wind, \
and the FAA ground stops and ground delay programs (GDPs) that follow, cut how many JetBlue flights can land and take \
off. Your job is to decide which flights to cancel and which to hold, watch by watch, so that passengers and the \
airline lose as little as possible over the day.

How the day runs:
- The day is played {watches}. The first message gives the day as the desk \
knows it at watch 0. Every tool call takes {CALL_MIN} minute of the watch; when the watch's time runs out, the next \
watch opens by itself, and advance opens it at once.
- What the desk knows: a flight's own departure delay (weather, air traffic control: anything but waiting for its \
aircraft or a slot) becomes known {LOOK // 60} hours before its scheduled departure; until then it is assumed on \
time. {news}, and the programs table shows the programs \
as issued, proposed GDPs included; a program may end earlier or later than issued.
- Ground delay programs: a GDP meters arrivals at a hub over its period. A flight into the hub from an origin in its \
scope, scheduled to land in the period, needs one of JetBlue's arrival slots in the program: it takes the earliest \
free slot at or after the time it could land and leaves at its EDCT, the slot less its scheduled block. A cancelled \
or late flight leaves its slot to the flights behind it. Arrivals outside a program fit under the hub's hourly rate.
- Ground stops: a flight to a stopped hub from an origin the stop covers waits on the ground until the stop ends; \
flights already airborne fly on.
- Departure caps: an airport may take only so many JetBlue departures in an hour (the departure caps table, known \
{CAP_LOOK // 60} hours ahead); a flight with no room in its hour tries again at the next hour's start.{layers}{crew}
- The env flies your plan on the day as it happens: each flight leaves at the earliest time its delay, its hold, a \
slot, a stop, a cap and an aircraft allow. It takes the first aircraft of its type ready at its origin (aircraft of \
one type at one airport are interchangeable; an aircraft needs its turn time on the ground between flights). A \
flight that can't leave by its deadline is cancelled automatically there. At the end of the day the env ferries each \
aircraft to the airport it is needed at tomorrow.
- Your decisions: cancel a flight (its aircraft stays on the ground, free for other flights, and its slot goes to the \
flights behind), restore a cancelled flight, or hold a flight until a time (not_before) so that its slot or aircraft \
goes to another flight first. A flight you don't touch leaves as early as it can.
- The freeze: at watch 0 nothing is frozen. From watch 1 on, flights your plan has leaving before the freeze line, \
{FREEZE} minutes after the watch opens, are frozen: they can't be changed, and stay frozen. A cancellation is final. \
The env holds any other flight your plan would have leaving before the freeze line to it, and refuses a change that \
would move a frozen flight or one that has already gone.

Tools:
- get_situation(): the time, the freeze line, the programs as issued with JetBlue's slots, the departure caps known \
so far{situation}, every flight (its status, its known delay, your plan, where your plan has it leave and land on \
the day as known, how late it lands, its aircraft, its slot and its crew), where the aircraft are, and the news so far.
- check_plan(changes): your changes over your plan, run on the day as known now: the plan's cost, the flights whose \
plan or times change, the automatic cancellations, the flights the env would hold to the freeze line, and the changes \
confirm_changes would refuse. It confirms nothing.
- confirm_changes(changes): commits the changes; flights you leave out keep their plan.
- advance(): ends the watch; its result has the new watch's news. After the last watch it returns "done": the day \
is graded on your plan.
A change is {{"flight": "f12", "action": "cancel" | "restore" | "hold" | "call_reserve" | "release_reserve", \
"not_before": "HH:MM" | null}}: cancel takes not_before null; restore and hold fly the flight, held until not_before \
(local time, "00:30+1" after midnight), or as early as it can with null. Where a crew base has a reserve pool (the \
situation's Reserve crews), a flight leaving it past its crew's deadline flies only on a reserve: call_reserve gives \
it one while the pool lasts, release_reserve hands it back before that deadline.

The cost, in passenger-minutes: every passenger's minutes of arrival delay; 523 for each passenger on a cancelled \
flight, plus one for every minute the cancellation is decided later than {NOTICE // 60} hours before its scheduled \
departure (an automatic cancellation is decided at its deadline, so it costs more than an early one); the airline's \
own aircraft minutes (a late departure costs crew and fixed costs, a long block fuel too, and a cancellation saves \
its fuel and maintenance); and the end-of-day ferries. A cancellation frees an aircraft and a slot; it pays when it \
saves the others more than it costs its own passengers. The day is graded once, on your final plan flown on the day \
as it really happened: the closer its cost to the best plan possible in hindsight, the higher the score. If you stop \
before the last watch, the rest of the day runs on your plan."""


def briefing(day: Day) -> str:
    """The opening prompt: the first watch's situation, the news so far included; it costs the day no time."""
    return f"{day.text()}\n\nIt is the first watch, {hhmm(day.t)}. Nothing is frozen yet: set the day up with " \
           "confirm_changes, then call advance."


def max_turns(day: Day) -> int:
    return TURNS_PER_WATCH * len(day.watches) + EXTRA_TURNS


def steps(task: Task, anchor: int | None = None, follower: int | None = None) -> list[dict]:
    """One task's bundle steps: deploy airops-v3-day, load the day (with the anchor's and the follower's costs when
    given, so the load needn't solve the anchor; without the anchor's the load step waits out the solve), play it
    with airops-llm on the gateway's clock frozen at the first watch, end the day and grade it."""
    day = Day(task)
    refs = {k: v for k, v in (("anchor", anchor), ("follower", follower)) if v is not None}
    solve = {} if anchor is not None else {"timeout_seconds": int(references.TIME_LIMIT) + LOAD_SLACK}
    return [
        {"id": "deploy", "type": "deploy_env", "env_id": ENV},
        {"id": "load-task", "type": "apply_server_config", "env_id": ENV,
         "directives": [{"service": ENV, "uri": LIVE_LOAD_URI, "args": {"task_id": task.task_id, **refs}}], **solve},
        {"id": "deploy-agent", "type": "deploy_agent", "a2a_agent_id": AGENT, "env_ids": [ENV]},
        {"id": "clock", "type": "sync_env_clock", "env_id": ENV, "virtual_time": virtual_time(task, day.t),
         "virtual_seconds_per_real_second": 0, "tolerate_missing_sync_time": False},
        {"id": "play", "type": "prompt_agent", "prompt_id": task.task_id, "system_prompt": rules(task),
         "prompt": briefing(day), "max_turns": max_turns(day), "model_params": {"max_tokens": 32000},
         "timeout_seconds": 7200},
        {"id": "end-day", "type": "apply_server_config", "env_id": ENV,
         "directives": [{"service": ENV, "uri": END_DAY_URI, "args": {}}]},
        {"id": "grade", "type": "env_outcome_verifier", "env_id": ENV, "file_artifact_id": VERIFIER,
         "verifier_id": "airops", "score_aggregator": "weighted_average"},
    ]
