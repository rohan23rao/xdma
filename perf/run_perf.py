#!/usr/bin/env python3
"""Performance sweeps (spec 16): runs the perf_* cocotb tests across their
parameter sweeps, writes CSVs and PNG plots into perf/out/, and checks the
pass thresholds (>= 90% utilization at latency 50 with defaults; fairness
within +-5%).

    python3 perf/run_perf.py [--jobs 4] [--only latency,burst,fair,desc,xlat]

Every number comes from the hardware counters (BYTES, BUSY_CYC, TLB_STATS)
and is cross-checked inside the tests against testbench timestamps (they
must agree within 1%).
"""
import argparse, csv, json, os, pathlib, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "perf" / "out"
sys.path.insert(0, str(ROOT / "tb"))
import run  # noqa: E402

LATS = [25, 50, 100, 200]
OUTS = [1, 2, 4, 8]
FIFOS = [32, 64, 128]
BURSTS = [4, 8, 16]

# Categorical palette (validated: dataviz validate_palette.js, light mode);
# every series is also direct-labelled and has its own marker.
COLORS = ["#257de0", "#f47230", "#00b894", "#f2a600"]
MARKERS = ["o", "s", "^", "D"]
INK, INK2, GRID = "#191919", "#666666", "#e5e5e5"


def job(test, params, env):
    """Run one perf test; returns its JSON result (or raises)."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        out = f.name
    cmd = [sys.executable, str(ROOT / "tb" / "run.py"), "--test", test, "--seed", "1"]
    if params:
        cmd += ["--params", ",".join(f"{k}={v}" for k, v in params.items())]
    e = dict(os.environ, XDMA_SKIP_BUILD="1", VERILATOR_JOBS="1", XDMA_PERF_OUT=out,
             **{k: str(v) for k, v in env.items()})
    p = subprocess.run(cmd, capture_output=True, text=True, env=e)
    if "1/1 tests passed" not in p.stdout:
        print("\n".join(p.stdout.splitlines()[-30:]))
        raise RuntimeError(f"{test} {params} {env} failed: {' '.join(cmd)}")
    with open(out) as f:
        res = json.load(f)
    os.unlink(out)
    return res


def run_all(work, jobs):
    for test, params, _ in work:
        run.build_for(test, params)
    with ThreadPoolExecutor(jobs) as ex:
        return list(ex.map(lambda w: job(*w), work))


def write_csv(name, rows):
    with open(OUT / name, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def style(ax, title, xlabel, ylabel):
    ax.set_title(title, color=INK, fontsize=11, loc="left")
    ax.set_xlabel(xlabel, color=INK2)
    ax.set_ylabel(ylabel, color=INK2)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2)


def plot_latency(rows):
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 4, figsize=(15, 3.8), sharey=True)
    for ax, mo in zip(axes, OUTS):
        for i, fd in enumerate(FIFOS):
            pts = sorted((r["latency"], 100 * r["util_hw"]) for r in rows
                         if r["max_out_ch"] == mo and r["fifo_depth"] == fd)
            xs, ys = zip(*pts)
            ax.plot(xs, ys, color=COLORS[i], marker=MARKERS[i], markersize=6, linewidth=2,
                    label=f"FIFO {fd} beats")
            ax.annotate(f"{fd}", (xs[-1], ys[-1]), xytext=(4, 0), textcoords="offset points",
                        color=INK2, fontsize=8, va="center")
        style(ax, f"MAX_OUT_CH = {mo}", "memory latency (cycles)", "utilization (% of 8 B/cycle)")
        ax.set_xticks(LATS)
        ax.set_ylim(0, 105)
    axes[0].legend(frameon=False, fontsize=8, loc="lower left")
    fig.suptitle("Latency tolerance, one MM2S channel (steady state, hardware counters)",
                 color=INK, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(OUT / "latency_tolerance.png", dpi=150)
    plt.close(fig)

    # headline: MAX_OUT_CH = 8, one line per FIFO depth, with the Little's-law
    # bound FIFO_DEPTH / (latency) for comparison
    fig, ax = plt.subplots(figsize=(6.5, 4))
    for i, fd in enumerate(FIFOS):
        pts = sorted((r["latency"], 100 * r["util_hw"]) for r in rows
                     if r["max_out_ch"] == 8 and r["fifo_depth"] == fd)
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color=COLORS[i], marker=MARKERS[i], markersize=7, linewidth=2,
                label=f"measured, FIFO {fd} beats ({8 * fd} B)")
        ax.plot(LATS, [min(100, 100 * fd / l) for l in LATS], color=COLORS[i], linewidth=1,
                linestyle="--")
        ax.annotate(f"{ys[-1]:.0f}%", (xs[-1], ys[-1]), xytext=(5, 0), textcoords="offset points",
                    color=INK, fontsize=8, va="center")
    ax.plot([], [], color=INK2, linestyle="--", linewidth=1, label="Little's law: FIFO bytes / (8 B x latency)")
    style(ax, "Utilization vs memory latency (MAX_OUT_CH = 8)", "memory latency (cycles)",
          "utilization (% of 8 B/cycle)")
    ax.set_xticks(LATS)
    ax.set_ylim(0, 105)
    ax.legend(frameon=False, fontsize=8, loc="lower left")
    fig.tight_layout()
    fig.savefig(OUT / "latency_fifo.png", dpi=150)
    plt.close(fig)


def plot_bars(name, title, labels, values, ylabel, notes=None, ymax=105, color=COLORS[0]):
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6, 3.6))
    bars = ax.bar(labels, values, color=color, width=0.55, edgecolor="white", linewidth=2)
    for i, (b, v) in enumerate(zip(bars, values)):
        txt = f"{v:.1f}" + (f"\n{notes[i]}" if notes and notes[i] else "")
        ax.annotate(txt, (b.get_x() + b.get_width() / 2, v), xytext=(0, 3), textcoords="offset points",
                    ha="center", va="bottom", color=INK, fontsize=8)
    style(ax, title, "", ylabel)
    ax.grid(False)
    ax.grid(True, axis="y", color=GRID, linewidth=0.8)
    ax.set_ylim(0, ymax)
    fig.tight_layout()
    fig.savefig(OUT / name, dpi=150)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--only", default="latency,burst,fair,desc,xlat")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    only = a.only.split(",")
    summary = {}
    ok = True

    if "latency" in only:
        work = [("perf_latency", dict(FIFO_DEPTH=fd, MAX_OUT_CH=mo), dict(XDMA_PERF_LAT=l))
                for fd in FIFOS for mo in OUTS for l in LATS]
        rows = run_all(work, a.jobs)
        write_csv("latency_tolerance.csv", rows)
        plot_latency(rows)
        summary["latency"] = rows

    if "burst" in only:
        work = [("perf_latency", dict(MAX_BURST=b), dict(XDMA_PERF_LAT=50)) for b in BURSTS]
        rows = run_all(work, a.jobs)
        write_csv("burst_length.csv", rows)
        plot_bars("burst_length.png", "Utilization vs MAX_BURST (latency 50, defaults otherwise)",
                  [str(r["max_burst"]) for r in rows], [100 * r["util_hw"] for r in rows],
                  "utilization (%)")
        summary["burst"] = rows

    if "fair" in only:
        res = run_all([("perf_saturate", {}, dict(XDMA_PERF_LAT=50))], 1)[0]
        rows = [dict(channel=c, direction="MM2S" if c < 2 else "S2MM", bytes=res["bytes"][c],
                     share_in_direction=res["share_in_direction"][c], share_total=res["share_total"][c],
                     util_hw=res["util_hw"][c], util_tb=res["util_tb"][c]) for c in range(len(res["bytes"]))]
        write_csv("fairness.csv", rows)
        plot_bars("fairness.png", "Share of its direction's bandwidth, all 4 channels saturating",
                  [f"ch{r['channel']} {r['direction']}" for r in rows],
                  [100 * r["share_in_direction"] for r in rows], "share (%)", ymax=60)
        summary["fair"] = rows

    if "desc" in only:
        work = [("perf_desc", dict(PREFETCH_EN=pf), dict(XDMA_PERF_LAT=50)) for pf in (1, 0)]
        res = run_all(work, a.jobs)
        rows = []
        for r in res:
            for shape in ("1x4KB", "64x64B"):
                rows.append(dict(prefetch=r["prefetch"], shape=shape, latency=r["latency"],
                                 util_e2e=r[shape]["util_e2e"], mean_gap_cycles=r[shape]["mean_gap"]))
        write_csv("desc_overhead.csv", rows)
        plot_bars("desc_overhead.png", "Descriptor overhead, 4 KB total (latency 50, end to end)",
                  [f"{r['shape']}\nprefetch {'on' if r['prefetch'] else 'off'}" for r in rows],
                  [100 * r["util_e2e"] for r in rows], "utilization (%)",
                  notes=[f"gap {r['mean_gap_cycles']:.0f} cyc" if r["mean_gap_cycles"] else "" for r in rows])
        summary["desc"] = rows

    if "xlat" in only:
        res = run_all([("perf_xlat", {}, dict(XDMA_PERF_LAT=50))], 1)[0]
        rows = [dict(mode=m, util_e2e=res[m]["util_e2e"], tlb_hits=res[m]["tlb_hits"],
                     tlb_misses=res[m]["tlb_misses"], hit_rate=res[m]["hit_rate"])
                for m in ("off", "linear", "stride4096")]
        write_csv("translation.csv", rows)
        plot_bars("translation.png", "Translation cost, 64 KB MM2S (latency 50, end to end)",
                  ["off", "on, linear", "on, STRIDE=4096"], [100 * r["util_e2e"] for r in rows],
                  "utilization (%)",
                  notes=["" if r["hit_rate"] is None else f"TLB hit {100 * r['hit_rate']:.1f}%" for r in rows])
        summary["xlat"] = rows

    with open(OUT / "summary.json", "w") as f:
        json.dump(summary, f, indent=1)

    # pass thresholds (spec 16)
    if "latency" in summary or "burst" in summary:
        rows = summary.get("latency", []) + summary.get("burst", [])
        d = [r for r in rows if r["latency"] == 50 and r["fifo_depth"] == 64 and r["max_out_ch"] == 8
             and r["max_burst"] == 16]
        if d:
            util = d[0]["util_hw"]
            print(f"threshold: utilization at latency 50, defaults = {100 * util:.1f}% (>= 90%)")
            ok &= util >= 0.90
    if "fair" in summary:
        worst = max(abs(r["share_in_direction"] - 0.5) / 0.5 for r in summary["fair"])
        print(f"threshold: worst fairness deviation = {100 * worst:.2f}% (<= 5%)")
        ok &= worst <= 0.05
    print("perf: PASS" if ok else "perf: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
