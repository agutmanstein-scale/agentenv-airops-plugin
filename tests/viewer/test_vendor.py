import json
import re
from pathlib import Path

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
