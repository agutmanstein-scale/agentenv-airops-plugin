"""The packaged pack's winter control played in process: both desks idle cost the pack's idle reference, and the
scripted desks (crew forwarding its system's words, ops accepting its cancellations) play stage 2b's talk-idle day."""

from occ_helpers import case, play, scripted

from agentenv_airops.occ.pack import PACK, Pack
from agentenv_airops.programs import crewplan, occ

CONTROL = "b6-20250115-0400"


def test_the_winter_control_idle_and_scripted_match_the_pack_and_stage_2b():
    w = Pack(PACK).world(CONTROL)
    idle = play(w)
    assert idle.grade["cost"] == w.refs["idle"] and idle.audit()["ok"]
    talk = play(w, policies=scripted("crew", "ops"))
    assert talk.grade["cost"] == round(crewplan.worth(occ.run(case(w), "talk-idle").day, False), 1)
    assert talk.audit()["ok"] and talk.acausal == []
