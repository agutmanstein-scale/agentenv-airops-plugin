"""The pinned inputs the Command Center model adds to step 0's: the airports' own movement counts, the runway-use wind
limits, the special weather reports and the radar.

Counts. JFK: the Port Authority's 2024 Airport Traffic Report (pinned PDF), p. 23, "Domestic and International"
monthly plane movements with the 2023-24 % change column; 2023 months are back-derived from that column (2024 / (1 +
change)), and December 2022 takes the nearest year's December (2023, back-derived). 'Other' movements (air taxi,
business, government, helicopters) are kept. BOS: Massport's monthly inbound flights, all users. Counted arrivals per
day are JFK's movements / 2 / days and BOS's inbound flights / days.

Wind limits: FAA Order 8400.9 (National Safety and Operational Criteria for Runway Use Programs, 11/9/81), par. 7d(1):
crosswind at most 20 kt and tailwind at most 5 kt on clear and dry runways (the Order counts gusts; the check applies
them to the sustained wind).

Weather: the routine METARs step 0 cached plus the SPECIs (IEM ASOS, report_type 4), a month and hub per file. Radar:
IEM's NEXRAD n0q composite (0.005 degree grid, dBZ = index / 2 - 32.5), one frame per 15-minute bin (the bin's start
plus 5 minutes, else its other two frames), reduced on download to the share of a 40 nm disc around each hub's
reference point at or above 20, 30, 40 and 50 dBZ, kept with each frame's URL and sha256, a JSON per local date.
"""

import calendar
import functools
import hashlib
import http.client
import io
import json
import math
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from ..build import bts, fetch
from ..build import weather as v1
from ..programs import candidates, unseen
from ..programs.advisories import HUBS, nm

ATR = "https://www.panynj.gov/content/dam/airports/statistics/statistics-general-info/annual-atr/ATR_2024.pdf"
ATR_SHA256 = "0f28d133faa2b4347e4bbc98d1e2eff6b1ea065ca4d97e8a9773319e4b493b59"
ATR_2024 = {1: (35_850, -4.8), 2: (34_228, -2.5), 3: (37_951, -7.6), 4: (38_661, -4.0), 5: (40_254, -4.5),
            6: (40_681, 1.1), 7: (42_627, 3.8), 8: (41_609, -2.9), 9: (39_931, 1.8), 10: (40_200, -1.3),
            11: (37_463, -3.2), 12: (39_115, -2.1)}
"""JFK plane movements per month of 2024 and the 2023-24 % change: ATR 2024, p. 23, Domestic and International."""
ORDER_8400_9 = "https://www.faa.gov/documentLibrary/media/Order/8400-9.pdf"
ORDER_8400_9_SHA256 = "dec12400db17edb269fe62e3d57c5185fee33e0522475a5d866f462ab24cf725"
CROSSWIND_KT, TAILWIND_KT = 20, 5
SPECI = v1.ASOS.replace("report_type=3", "report_type=4")
N0Q = "https://mesonet.agron.iastate.edu/archive/data/{d:%Y/%m/%d}/GIS/uscomp/n0q_{d:%Y%m%d%H%M}.png"
N0Q_WEST, N0Q_NORTH, N0Q_PIXEL = -126.0, 50.0, 0.005
DISC_NM = 40.0
THRESHOLDS = (20, 30, 40, 50)
BIN = 15
HOURS_AFTER = 4
ARP = {"JFK": (40.63992805, -73.77869222), "BOS": (42.36294444, -71.00638888)}
"""Airport reference points, NASR APT_BASE (01 Oct 2026 cycle)."""


def atr_path(inputs: Path) -> Path:
    return inputs / "panynj" / "ATR_2024.pdf"


def order_path(inputs: Path) -> Path:
    return inputs / "faa" / "8400-9.pdf"


def speci_path(inputs: Path, hub: str, month: str) -> Path:
    return inputs / "iem" / "speci" / f"{hub}_{month.replace('-', '_')}.csv"


def radar_path(inputs: Path, d: str) -> Path:
    return inputs / "iem" / "n0q" / f"{d}.json"


def pin(inputs: Path, path: Path, url: str, sha: str) -> Path:
    if not path.exists():
        fetch.save(path, fetch.get(url, timeout=300))
        fetch.record(inputs, str(path.relative_to(inputs)), url, bts.today())
    if fetch.sha256(path) != sha:
        raise ValueError(f"{path} does not match its pinned sha256")
    return path


def pin_documents(inputs: Path) -> list[Path]:
    return [pin(inputs, atr_path(inputs), ATR, ATR_SHA256), pin(inputs, order_path(inputs), ORDER_8400_9,
                                                                    ORDER_8400_9_SHA256)]


def counted_per_day(hub: str, month: str) -> float:
    """The airport's counted arrivals per day in the month."""
    year, m = (int(v) for v in month.split("-"))
    days = calendar.monthrange(year, m)[1]
    if hub == "BOS":
        return unseen.MASSPORT_ARRIVALS[month][0] / days
    if year == 2024:
        return ATR_2024[m][0] / 2 / days
    if year == 2023 or month == "2022-12":
        total, change = ATR_2024[m]
        return total / (1 + change / 100) / 2 / days
    raise ValueError(f"no JFK movement count for {month}")


def fetch_specis(inputs: Path, months: Iterable[str]) -> list[Path]:
    out = []
    for hub in HUBS:
        for month in months:
            path = speci_path(inputs, hub, month)
            if not path.exists():
                sts, ets = v1.span(month)
                url = SPECI.format(station=hub, sts=sts, ets=ets)
                fetch.save(path, fetch.get(url, pause=v1.PAUSE))
                fetch.record(inputs, str(path.relative_to(inputs)), url, bts.today())
            out.append(path)
    return out


def frame_times(d: str) -> list[int]:
    """The UTC epoch minute of each bin's first-choice frame from local midnight of d through HOURS_AFTER past the
    next midnight."""
    base = candidates.midnight(d)
    return [base + BIN * b + 5 for b in range((24 + HOURS_AFTER) * 60 // BIN)]


def fetch_radar(inputs: Path, dates: Sequence[str], workers: int = 8) -> list[Path]:
    """Each date's radar JSON, a frame per bin, dates in parallel processes, each written as it completes; a bin whose
    frames are all missing is recorded with null shares."""
    todo = [d for d in sorted(set(dates)) if not radar_path(inputs, d).exists()]
    with ProcessPoolExecutor(workers) as pool:
        jobs = {pool.submit(radar_day, d): d for d in todo}
        for job in as_completed(jobs):
            d = jobs[job]
            path = radar_path(inputs, d)
            doc = {"date": d, "disc_nm": DISC_NM, "thresholds_dbz": list(THRESHOLDS), "arp": ARP,
                   "frames": job.result()}
            fetch.save(path, (json.dumps(doc) + "\n").encode())
            fetch.record(inputs, str(path.relative_to(inputs)), N0Q.split("{")[0], bts.today())
            print("radar", d, flush=True)
    return [radar_path(inputs, d) for d in sorted(set(dates))]


def radar_day(d: str) -> list[dict]:
    return [frame(t) for t in frame_times(d)]


def frame(t: int) -> dict:
    for k in (0, -5, 5):
        stamp = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(minutes=t + k)
        url = N0Q.format(d=stamp)
        try:
            body = fetch.get(url, attempts=3, timeout=120)
        except (OSError, http.client.HTTPException):
            continue
        return {"t": t, "utc": f"{stamp:%Y-%m-%dT%H:%MZ}", "url": url, "sha256": hashlib.sha256(body).hexdigest(),
                "shares": shares(body, discs())}
    return {"t": t, "utc": None, "url": None, "sha256": None, "shares": None}


@functools.cache
def discs() -> dict[str, tuple[slice, slice, np.ndarray]]:
    """Per hub, the n0q rows and columns of a box around its disc and the disc's mask inside the box."""
    out = {}
    for hub, (lat, lon) in ARP.items():
        dlat = DISC_NM / 60
        dlon = dlat / math.cos(math.radians(lat))
        r0, r1 = int((N0Q_NORTH - lat - dlat) / N0Q_PIXEL) - 1, int((N0Q_NORTH - lat + dlat) / N0Q_PIXEL) + 2
        c0, c1 = int((lon - dlon - N0Q_WEST) / N0Q_PIXEL) - 1, int((lon + dlon - N0Q_WEST) / N0Q_PIXEL) + 2
        lats = N0Q_NORTH - (np.arange(r0, r1) + 0.5) * N0Q_PIXEL
        lons = N0Q_WEST + (np.arange(c0, c1) + 0.5) * N0Q_PIXEL
        mask = np.array([[nm((a, b), (lat, lon)) <= DISC_NM for b in lons] for a in lats])
        out[hub] = (slice(r0, r1), slice(c0, c1), mask)
    return out


def shares(body: bytes, masks: dict) -> dict[str, dict[str, float]]:
    from PIL import Image  # an extra's: the path helpers above serve installs without it (programs.layers)

    Image.MAX_IMAGE_PIXELS = None
    im = Image.open(io.BytesIO(body))
    if im.mode != "P":
        raise ValueError(f"expected a palette PNG, got {im.mode}")
    idx = np.asarray(im)
    out = {}
    for hub, (rows, cols, mask) in masks.items():
        dbz = idx[rows, cols].astype(float) * 0.5 - 32.5
        inside = dbz[mask]
        out[hub] = {str(z): round(float((inside >= z).mean()), 5) for z in THRESHOLDS}
    return out


def load_radar(inputs: Path, d: str) -> dict[int, dict]:
    """The date's frames by UTC epoch minute of their bin's first choice."""
    return {f["t"]: f for f in json.loads(radar_path(inputs, d).read_text())["frames"]}
