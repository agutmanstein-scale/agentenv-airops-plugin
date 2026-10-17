"""The programs tests' hand-made days and fixture slice, shared with the playable day's tests, and the fake LiteLLM
and airops-llm the live tests play with."""

import importlib.util
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parents[1]
PROGRAMS = TESTS / "programs"
sys.path[:0] = [str(PROGRAMS), str(TESTS), str(TESTS / "agent")]
_spec = importlib.util.spec_from_file_location("programs_conftest", PROGRAMS / "conftest.py")
_programs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_programs)
slice_inputs, fits, built, task, crewed = (_programs.slice_inputs, _programs.fits, _programs.built, _programs.task,
                                           _programs.crewed)


@pytest.fixture
def anyio_backend():
    return "asyncio"
