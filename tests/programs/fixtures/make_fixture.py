"""Cut the committed NASR slice (tests/programs/fixtures/apt_base.csv) from the pinned APT_BASE: the airport id, ICAO id
and responsible ARTCC of every airport in v1's fixture slice of BTS (tests/pack/fixtures/inputs), so a test can build a
v2 task offline from that slice.

    python tests/programs/fixtures/make_fixture.py [cache/inputs]
"""

import csv
import io
import sys
import zipfile
from pathlib import Path

import pandas as pd

from agentenv_airops.build import bts
from agentenv_airops.programs import advisories

ROOT = Path(__file__).resolve().parents[3]
SLICE = ROOT / "tests" / "pack" / "fixtures" / "inputs"
OUT = Path(__file__).resolve().parent / "apt_base.csv"
COLUMNS = ["SITE_TYPE_CODE", "ARPT_ID", "ICAO_ID", "RESP_ARTCC_ID"]


def main(inputs: Path) -> None:
    with zipfile.ZipFile(bts.ontime_path(SLICE, "2023-06")) as z:
        df = pd.read_csv(z.open(z.namelist()[0]), dtype=str)
    codes = set(df.Origin) | set(df.Dest)
    icao = {bts.airports()[c]["icao"] for c in codes}
    with zipfile.ZipFile(advisories.nasr_path(inputs)) as z:
        rows = [r for r in csv.DictReader(io.TextIOWrapper(z.open("APT_BASE.csv"), "latin-1"))
                if r["SITE_TYPE_CODE"] == "A" and (r["ARPT_ID"] in codes or r["ICAO_ID"] in icao)]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    w.writerows(sorted(rows, key=lambda r: r["ARPT_ID"]))
    OUT.write_text(buf.getvalue())


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "cache" / "inputs")
