import importlib.util

from program_helpers import ROOT

from agentenv_airops.programs.repairs import CUTOFF

SPEC = importlib.util.spec_from_file_location("step0", ROOT / "scripts" / "step0.py")
step0 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(step0)


def test_without_train_days_the_fixed_parameters_and_g2_are_marked_not_out_of_sample(capsys):
    provisional = {"JFK": 0.45, "BOS": 0.5}
    fix = step0.fixed([], [], [], provisional, 1.29)
    assert (fix["scale"], fix["cutoff"], fix["tolerance"]) == (provisional, CUTOFF, 1.29)
    assert fix["out_of_sample"] == {"scale": {"JFK": False, "BOS": False}, "cutoff": False, "tolerance": False}
    assert "WARNING: not out of sample" in capsys.readouterr().err
    fid = {"a": 1.0, "b": 1.1, "c": 0.9}
    split = {"a": "eval", "b": "train", "c": "train"}
    assert step0.fidelity_gate(fid, split, True)["pass"]
    gate = step0.fidelity_gate(fid, split, False)
    assert (gate["eval"], gate["train"], gate["out_of_sample"], gate["pass"]) == (1.0, 1.0, False, False)
