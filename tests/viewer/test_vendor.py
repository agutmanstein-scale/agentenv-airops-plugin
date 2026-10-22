import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web"
OURS = [p for p in WEB.rglob("*.js") if "vendor" not in p.relative_to(WEB).parts[:1]]


def test_the_import_map_and_the_page_point_at_the_vendored_copies_only():
    html = (WEB / "index.html").read_text()
    imports = json.loads(re.search(r'<script type="importmap">(.*?)</script>', html, re.S).group(1))["imports"]
    assert set(imports) == {"maplibre-gl", "three", "three/addons/"}
    for url in imports.values():
        assert url.startswith("./vendor/") and (WEB / url).exists()
    assert not re.findall(r'(?:href|src)="(?:https?:)?//', html)


def test_every_bare_import_resolves_to_a_vendored_file_and_nothing_loads_code_from_a_cdn():
    addons = {m for p in OURS for m in re.findall(r'from "three/addons/([^"]+)"', p.read_text())}
    assert addons and all((WEB / "vendor/three-0.180.0/examples/jsm" / a).is_file() for a in addons)
    for p in OURS:
        assert not re.search(r"cdn\.jsdelivr|unpkg\.com|esm\.sh|cdnjs|skypack", p.read_text()), p
        assert not re.search(r"""import\(\s*["'`]https?:""", p.read_text()), p
    for name in ("three-0.180.0/LICENSE", "maplibre-gl-5.24.0/LICENSE.txt", "topojson-client-3.1.0/LICENSE",
                 "us-atlas-3.0.1/LICENSE", "world-atlas-2.0.2/LICENSE"):
        assert (WEB / "vendor" / name).is_file()


@pytest.mark.skipif(not shutil.which("git") or not (WEB.parent / ".git").exists(), reason="not a git checkout")
def test_no_vendored_file_is_left_out_of_the_repository_by_gitignore():
    files = [p.relative_to(WEB.parent).as_posix() for p in (WEB / "vendor").rglob("*") if p.is_file()]
    ignored = subprocess.run(["git", "check-ignore", "--no-index", *files], cwd=WEB.parent, capture_output=True,
                             text=True).stdout.split()
    assert files and not ignored


def test_every_sound_file_is_shipped_listed_with_its_cc0_source_and_small():
    files = set(re.findall(r'"([\w-]+\.ogg)"', (WEB / "audio.js").read_text()))
    shipped = {p.name for p in (WEB / "audio").glob("*.ogg")}
    sources = (WEB / "audio" / "SOURCES.md").read_text()
    assert files and files == shipped
    for name in shipped:
        row = next(r for r in sources.splitlines() if r.startswith(f"| `{name}` |"))
        assert re.search(r"\(https://freesound\.org/people/[^/]+/sounds/\d+/\)", row), name
    assert "CC0 1.0" in sources and "creativecommons.org/publicdomain/zero/1.0" in sources
    assert sum(p.stat().st_size for p in (WEB / "audio").iterdir()) < 15e6
