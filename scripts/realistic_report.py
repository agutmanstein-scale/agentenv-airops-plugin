"""The realism report over realistic_compare.py's output: per mode (current, realistic) the fidelity of JetBlue's
replay against JetBlue's recorded day, the outcome counts of every scenario, the diagnostics, and the realistic reward.

Usage: python3 -I scripts/realistic_report.py REALREP_DIR [--late 15] [--bestj BESTJ_DIR]
"""

import gzip
import json
import math
import sys
from pathlib import Path

rep = Path(sys.argv[1])
LATE = int(sys.argv[sys.argv.index("--late") + 1]) if "--late" in sys.argv else 15
BESTJ = Path(sys.argv[sys.argv.index("--bestj") + 1]) if "--bestj" in sys.argv else None
DATES = Path(__file__).resolve().parents[1] / "data" / "b6-v3" / "dates"
LEADS = ("jbsim@120", "jbsim@360", "jbsim@start", "jbtails@360", "jbfull@360")
MODES = (("now", "current"), ("real", "realistic"))
ROWS = ("recorded", "jbsim", "desks", "model", "best")
JB = "jbfull@360"
J_ROWS = ("desks", "model", "best", "cut@2h", "cut@4h", "cut@6h", "best J")
USD = 1.07
LOST = 50e6 / (4100 * 7_333_000 / 68_152 * 167.69)
KEPT = 35 / 50


def score(cost, anchor, k=2.0):
    return 0.2 + 0.8 / (1 + 5 / 3 * (max(cost, 0) / anchor) ** k)


def take_of(days):
    """The share of the fare JetBlue loses on a passenger stranded to a later day, such that on JetBlue's replay over
    `days` (the refunded at the full fare) it loses LOST of the fares booked on its cancelled flights: JetBlue's Q1
    2014 storms, 4,100 cancellations, $50M revenue, 107.6 passengers a departure at a $167.69 average fare; each
    itinerary's fare by its distance (realistic.od_fare)."""
    num = den = 0.0
    for _, row in days:
        a = row["real"][JB]["airline"]
        num += LOST * a["cancelled_rev"] - a["refunded_rev"]
        den += a["stranded_rev"]
    return num / den


def save_of(days):
    """The share of FAA's fuel and maintenance rate a cancellation saves, such that on JetBlue's replay over `days` it
    saves 1 - KEPT of the revenue it loses: JetBlue's Q1 2014 storms cost $50M of revenue and $35M of operating
    income."""
    rev = sum(LOST * row["real"][JB]["airline"]["cancelled_rev"] for _, row in days)
    return (1 - KEPT) * rev / sum(row["real"][JB]["airline"]["saved"] for _, row in days)


def jetblue(a, take, save):
    return a["aircraft"] + (1 - save) * a["saved"] + a["crew"] + a["tomorrow"] + lost(a, take)


def lost(a, take):
    return a["refunded_rev"] + take * a["stranded_rev"]


def pearson(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    sx, sy = math.sqrt(sum((x - mx) ** 2 for x in xs)), math.sqrt(sum((y - my) ** 2 for y in ys))
    return sxy / (sx * sy) if sx and sy else float("nan")


def recorded(f):
    a = f["actual"]
    return None if a["cancelled"] or a["diverted"] or a["arr"] is None else a["arr"]


def simmed(x):
    return x["arr"] if x["status"] == "flown" else None


def outcome(sched, out):
    c = {"cancelled": 0, "late15": 0, "late60": 0, "late180": 0, "minutes": 0, "flown": 0}
    for i, f in sched.items():
        if i not in out:
            continue
        a = out[i]
        if a is None:
            c["cancelled"] += 1
            continue
        d = max(0, a - f["sched_arr"])
        c["flown"] += 1
        c["minutes"] += d
        c["late15"] += d >= LATE
        c["late60"] += d >= 60
        c["late180"] += d >= 180
    return c


def fidelity(days, mode, lead):
    pairs, auto, flew = [], 0, 0
    rec15 = sim15 = rec60 = sim60 = 0
    for sched, row in days:
        js = row[mode][lead]["flights"]
        for i, f in sched.items():
            r = recorded(f)
            if r is None:
                continue
            flew += 1
            s = simmed(js[str(i)])
            if s is None:
                auto += 1
                continue
            dr, ds = max(0, r - f["sched_arr"]), max(0, s - f["sched_arr"])
            pairs.append((dr, ds))
            rec15 += dr >= LATE
            sim15 += ds >= LATE
            rec60 += dr >= 60
            sim60 += ds >= 60
    xs, ys = zip(*pairs, strict=True)
    return {"corr": pearson(xs, ys), "mae": sum(abs(a - b) for a, b in pairs) / len(pairs),
            "late15": sim15 / rec15 - 1, "late60": sim60 / rec60 - 1, "unflown": auto / flew, "flew": flew}


def report_j(days, take, save):
    print(f"\nJetBlue cost J (0.5 = JetBlue's replay, {JB}): aircraft and crew minutes (a cancellation saving "
          f"{save:.2f} of FAA's fuel and maintenance), tomorrow at the aircraft's cost, lost revenue (refunded at the "
          f"fare, stranded at {take:.2f} of it); no passenger time")
    print(f"  {'date':10} {'JB $M':>7} " + " ".join(f"{r:>8}" for r in J_ROWS))
    agg = {r: [] for r in J_ROWS}
    for _, row in days:
        real = row["real"]
        anchor = jetblue(real[JB]["airline"], take, save)
        cells = []
        for r in J_ROWS:
            if r in real:
                v = score(jetblue(real[r]["airline"], take, save), anchor)
                agg[r].append(v)
                cells.append(f"{v:8.3f}")
            else:
                cells.append(f"{'-':>8}")
        print(f"  {row['date']:10} {anchor * USD / 1e6:7.2f} " + " ".join(cells))
    print(f"  {'mean':10} {'0.500':>7} " + " ".join(f"{sum(v) / len(v):8.3f}" if v else f"{'-':>8}"
                                                     for v in agg.values()))
    print("\nrescaled JetBlue reward: 0.5 at JetBlue's replay, 1.0 at the cheapest plan known under J (any row)")
    print(f"  {'date':10} {'gap $M':>7} " + " ".join(f"{r:>8}" for r in J_ROWS) + f" {'JB':>8}")
    agg = {r: [] for r in J_ROWS + (JB,)}
    for _, row in days:
        real = row["real"]
        js = {r: jetblue(real[r]["airline"], take, save) for r in J_ROWS + (JB,) if r in real}
        top, anchor = min(js.values()), js[JB]
        cells = []
        for r in J_ROWS + (JB,):
            if r in js:
                v = score(js[r] - top, max(anchor - top, 1.0))
                agg[r].append(v)
                cells.append(f"{v:8.3f}")
            else:
                cells.append(f"{'-':>8}")
        print(f"  {row['date']:10} {(anchor - top) * USD / 1e6:7.2f} " + " ".join(cells))
    print(f"  {'mean':10} {'':>7} " + " ".join(f"{sum(v) / len(v):8.3f}" if v else f"{'-':>8}" for v in agg.values()))
    print(f"\n  per day, mean $k     {'cancelled':>9} {'aircraft':>9} {'crew':>7} {'tomorrow':>9} {'lost rev':>9} "
          f"{'total':>8}")
    rec = sum(sum(1 for f in sched.values() if recorded(f) is None) for sched, _ in days) / len(days)
    print(f"  {'recorded':20} {rec:9.1f}")
    for r in (JB,) + J_ROWS:
        xs = [row["real"][r]["airline"] for _, row in days if r in row["real"]]
        if not xs:
            continue
        n = len(xs)
        canc = sum(sum(1 for x in row["real"][r]["flights"].values() if x["status"] != "flown")
                   for _, row in days if r in row["real"]) / n
        parts = ([sum(a["aircraft"] + (1 - save) * a["saved"] for a in xs) / n]
                 + [sum(a[k] for a in xs) / n for k in ("crew", "tomorrow")] + [sum(lost(a, take) for a in xs) / n])
        print(f"  {r:20} {canc:9.1f} " + " ".join(f"{v * USD / 1e3:9.0f}" for v in parts) +
              f" {sum(parts) * USD / 1e3:8.0f}")


def report(name, days, take=None, save=None):
    if not days:
        return
    print(f"\n{'=' * 100}\n{name}: {len(days)} days ({', '.join(r['date'] for _, r in days)})")
    lead = {}
    print("\nfidelity: JetBlue's cancellations replayed vs JetBlue's recorded day (flights JetBlue flew)")
    print(f"  {'mode':10} {'lead':12} {'corr':>6} {'MAE min':>8} {f'{LATE}+ late':>9} {'60+ late':>9} {'unflown':>8}")
    for mode, label in MODES:
        fits = {k: fidelity(days, mode, k) for k in LEADS}
        lead[mode] = max(fits, key=lambda k: fits[k]["corr"])
        for k, x in fits.items():
            mark = " <- used" if k == lead[mode] else ""
            print(f"  {label:10} {k:12} {x['corr']:6.3f} {x['mae']:8.1f} {100 * x['late15']:+8.1f}% "
                  f"{100 * x['late60']:+8.1f}% {100 * x['unflown']:7.2f}%{mark}")
    for metric, label in (("cancelled", "cancelled"), ("late15", f"{LATE}+ min late"), ("late60", "60+ min late"),
                          ("late180", "3 h+ late"), ("avg", "avg delay (min)")):
        print(f"\n{label}")
        print(f"  {'date':10} " + " ".join(f"{m[:4] + ':' + r:>13}" for m, _ in MODES for r in ROWS))
        tot = {}
        for sched, row in days:
            cells = []
            for mode, _ in MODES:
                outs = {"recorded": {i: recorded(f) for i, f in sched.items()}}
                for r in ROWS[1:]:
                    key = lead[mode] if r == "jbsim" else r
                    if key in row[mode]:
                        outs[r] = {int(i): simmed(x) for i, x in row[mode][key]["flights"].items()}
                for r in ROWS:
                    if r not in outs:
                        cells.append("-")
                        continue
                    c = outcome(sched, outs[r])
                    t = tot.setdefault((mode, r), {k: 0 for k in c})
                    for k, v in c.items():
                        t[k] += v
                    cells.append(f"{c['minutes'] / c['flown']:.0f}" if metric == "avg" else str(c[metric]))
            print(f"  {row['date']:10} " + " ".join(f"{c:>13}" for c in cells))
        cells = []
        for mode, _ in MODES:
            for r in ROWS:
                t = tot.get((mode, r))
                cells.append("-" if t is None else f"{t['minutes'] / t['flown']:.0f}" if metric == "avg"
                             else str(t[metric]))
        print(f"  {'total':10} " + " ".join(f"{c:>13}" for c in cells))
    print("\ndiagnostic: of the flights JetBlue cancelled, the desks fly them ... (never a target)")
    for mode, label in MODES:
        n = under = cant = 0
        for sched, row in days:
            for i, f in sched.items():
                if recorded(f) is not None:
                    continue
                n += 1
                x = row[mode]["desks"]["flights"][str(i)]
                if x["status"] != "flown":
                    cant += 1
                elif x["dep"] - f["sched_dep"] < 60:
                    under += 1
        print(f"  {label:10} under 1 h late {under}/{n} ({100 * under / n:.0f}%), not at all {cant}")
    for price, label in (("realistic", "cost P: the passenger model"), ("tdi", "TDI: 457 min per cancelled passenger")):
        print(f"\nrealistic reward, {label} (0.5 = JetBlue's replay at {lead['real']}) | current reward")
        print(f"  {'date':10} {'JB replay':>11} {'desks':>11} {'model':>11} {'best':>11} | {'cur model':>9} "
              f"{'cur desks':>9}")
        agg = {"desks": [], "model": [], "best": []}
        for _, row in days:
            real = row["real"]
            anchor = real[lead["real"]]["cost"][price]
            best, b0 = row["refs"]["best"], row["refs"]["B0"]

            def current(c, best=best, b0=b0):
                return 0.2 + 0.8 * math.exp(-max(0, c - best) / max(b0 - best, 0.1 * best) / 0.5)

            cells = []
            for r in ("desks", "model", "best"):
                if r in real:
                    s = score(real[r]["cost"][price], anchor)
                    agg[r].append(s)
                    cells.append(f"{s:11.3f}")
                else:
                    cells.append(f"{'-':>11}")
            cm = f"{current(row['now']['model']['cost']['v3']):9.3f}" if "model" in row["now"] else f"{'-':>9}"
            print(f"  {row['date']:10} {anchor / 1e6:9.2f}M " + " ".join(cells) +
                  f" | {cm} {current(row['now']['desks']['cost']['v3']):9.3f}")
        print(f"  {'mean':10} {'0.500':>11} " + " ".join(f"{sum(v) / len(v):11.3f}" if v else f"{'-':>11}"
                                                        for v in agg.values()))
    if take is not None and all("airline" in row["real"].get(JB, {}) for _, row in days):
        report_j(days, take, save)
    print("\nrealistic layers per day: dep-cap hours, outstation arr-cap hours, closures, aircraft broken")
    for _, row in days:
        x = row["realistic"]
        print(f"  {row['date']:10} {x['dep_caps']:>4} {x['arr_caps']:>5} {x['closures']:>3} {len(x['broken']):>3}")


days = []
for p in sorted(rep.glob("*.json")):
    row = json.load(open(p))
    sched = {f["id"]: f for f in json.load(gzip.open(DATES / f"{row['date']}.json.gz"))["task"]["flights"]}
    if BESTJ and (b := BESTJ / p.name).exists():
        x = json.load(open(b))
        row["real"]["best J"] = {"airline": x["airline"], "flights": x["flights"]}
    days.append((sched, row))
stream = [d for d in days if "model" in d[1]["now"]]
take = take_of(stream) if all("saved" in r["real"].get(JB, {}).get("airline", {}) for _, r in stream) else None
save = save_of(stream) if take is not None else None
if take is not None:
    print(f"lost revenue: LOST = {LOST:.3f} of the fare per passenger booked on a cancelled flight (JetBlue Q1 2014); "
          f"stranded take {take:.3f}, saving {save:.3f} of FAA's rate, fitted on the stream days")
report("stream days (train)", stream, take, save)
report("held-out days (eval)", [d for d in days if d[1]["split"] == "eval"], take, save)
