"""The static Space in headless Chrome: its page opens the viewer on a dataset served from another origin with the CORS
headers huggingface.co sends, lists the dataset's episodes and plays one; and huggingface.co's resolve URLs, a plain
file and a xet-stored one, are readable from another origin."""

import json
from pathlib import Path
from urllib.parse import quote

import pytest
from browser import chrome, served

from agentenv_airops import export
from agentenv_airops.core.model import load_pack
from agentenv_airops.episodes import replay

ROOT = Path(__file__).resolve().parents[2]
E2E = ROOT / "tests" / "export" / "fixtures"
RESOLVE = "https://huggingface.co/datasets/earakely-scale/AirOpsEnv-AgentEnv/resolve/v0.1.0"


@pytest.mark.browser
def test_the_space_plays_the_datasets_episodes_from_another_origin(hub, tmp_path):
    [task] = load_pack(E2E / "e2e-day.jsonl")
    played = replay(task, json.loads((E2E / "e2e-transcript.json").read_text())["messages"])
    episode = export.episode(task, played.day.data(), played.steps, model="anthropic/claude-sonnet-5-5", run="e2e",
                             cache=tmp_path / "cache")
    dataset, space = tmp_path / "dataset", tmp_path / "space"
    hub.write({f"viewer/{p}": c for p, c in export.folder([episode], tmp_path / "cache").items()}, dataset)
    hub.write(hub.space_files("main"), space)
    with served(dataset, cors=True) as data, served(space) as site, chrome() as page:
        page.goto(f"{site}/index.html?index={quote(data + '/viewer/episodes/index.json')}")
        assert page.js("airops.ready") == {"episodes": 1}
        assert page.js("location.pathname") == "/viewer/index.html"
        link = page.js("document.querySelector('a.ep-card').href")
        assert export.file_name(episode) in link
        page.goto(link)
        ready = page.js("airops.ready")
        assert ready["steps"] == len(episode["steps"]) and ready["watches"] == [w["t"] for w in episode["watches"]]
        assert "Claude Sonnet 5.5" in page.js("document.querySelector('.head h1').textContent")


@pytest.mark.browser
@pytest.mark.network
def test_huggingface_resolve_urls_are_readable_from_another_origin(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>blank</title>")
    with served(tmp_path) as site, chrome() as page:
        page.goto(f"{site}/index.html", "document.readyState === 'complete'")
        for name in ("results/eval-v1.json", "results/board.parquet"):
            ok, status, size = page.js(f"fetch('{RESOLVE}/{name}').then(async r => [r.ok, r.status, "
                                       "(await r.arrayBuffer()).byteLength])")
            assert ok and status == 200 and size > 1000, name
