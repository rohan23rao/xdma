#!/usr/bin/env python3
"""Open-source synthesis flow (DECISIONS.md D-001): Yosys (yosys-slang front
end, ABC technology mapping) onto ASAP7 RVT / TT / NLDM, OpenSTA for timing
and power. All results are "Yosys + OpenSTA, ASAP7, post-synthesis".

    python3 syn/yosys/run_syn.py            # full flow (make syn)
    python3 syn/yosys/run_syn.py --period 500 --no-sweep

Steps:
1. Clock sweep: for P = 1000, 950, ... ps, synthesize with an ABC delay
   target of P and run OpenSTA at P (I/O delays 20% of P, BUFx2 drive,
   4 x BUFx2 load). Stop at the first P with WNS < 0; the fastest passing P
   is the result.
2. At that period: area per block (hierarchy kept), top-10 timing paths,
   and power with clock gating inferred (Yosys `clockgate`, ASAP7 ICG
   cells) and without, at measured activity (VCD from perf_saturate) and
   at idle (VCD from an idle run).
Outputs: syn/out/ (reports, netlists, summary.json).
"""
import argparse, json, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
OUT = ROOT / "syn" / "out"
LIB = ROOT / "syn" / "lib" / "asap7"
RTL_FIRST = "rtl/xdma_pkg.sv"

LIBS = ["asap7sc7p5t_AO_RVT_TT_nldm_211120.lib", "asap7sc7p5t_INVBUF_RVT_TT_nldm_220122.lib",
        "asap7sc7p5t_OA_RVT_TT_nldm_211120.lib", "asap7sc7p5t_SIMPLE_RVT_TT_nldm_211120.lib",
        "asap7sc7p5t_SEQ_RVT_TT_nldm_220123.lib"]
SEQ = "asap7sc7p5t_SEQ_RVT_TT_nldm_220123.lib"
# Cells ORFS keeps out of ASAP7 designs (fractional drive strengths, scan
# flops), the async set/reset flop (the design uses synchronous reset only),
# and the duty-cycle-corrected ICGs.
DONT_USE = ["*x1p*_ASAP7*", "*xp*_ASAP7*", "SDF*", "DFFASRHQNx1_ASAP7_75t_R", "ICG*DC_ASAP7*"]


def rtl_files():
    files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "rtl").glob("*.sv"))
    files.remove(RTL_FIRST)
    return [RTL_FIRST] + files


def yosys_script(period, gate, tag, params):
    libs = " ".join(f"-liberty syn/lib/asap7/{l}" for l in LIBS)
    du = " ".join(f"-dont_use {d}" for d in DONT_USE)
    gparams = " ".join(f"-G {k}={v}" for k, v in params.items())
    lines = [
        f"read_slang -j 1 --keep-hierarchy {gparams} --top xdma_top {' '.join(rtl_files())}",
        "hierarchy -check -top xdma_top",
        "synth -top xdma_top",
    ]
    if gate:
        # one ICG per group of >= 4 flops sharing clock and enable
        lines.append(f"clockgate -liberty syn/lib/asap7/{SEQ} -dont_use ICG*DC_ASAP7* -min_net_size 4")
    lines += [
        f"dfflibmap -liberty syn/lib/asap7/{SEQ} {du}",
        # ABC maps, then buffers high-fanout nets and sizes gates (the
        # buffer/upsize/dnsize steps of the OpenROAD-flow-scripts ASAP7 flow);
        # without them a 64:1 FIFO read mux leaves a ~670 fF net unbuffered.
        f"abc -D {period} -constr syn/yosys/abc.constr -script syn/yosys/abc_speed.script {libs} {du}",
        "opt_clean -purge",
        "hilomap -singleton -hicell TIEHIx1_ASAP7_75t_R H -locell TIELOx1_ASAP7_75t_R L",
        "opt_clean -purge",
        f"tee -q -o syn/out/area_{tag}.rpt stat -liberty syn/lib/asap7/{LIBS[0]} "
        + " ".join(f"-liberty syn/lib/asap7/{l}" for l in LIBS[1:]),
        f"write_verilog -noattr -noexpr -nohex -nodec syn/out/netlist_{tag}.v",
    ]
    return "\n".join(lines) + "\n"


def synth(period, gate, tag, params):
    ys = OUT / f"synth_{tag}.ys"
    ys.write_text(yosys_script(period, gate, tag, params))
    p = subprocess.run(["yosys", "-q", "-l", f"syn/out/synth_{tag}.log", str(ys.relative_to(ROOT))],
                       cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stdout[-3000:], p.stderr[-3000:])
        raise RuntimeError(f"yosys failed ({tag}); see syn/out/synth_{tag}.log")


def sta(period, tag, act=None, scope=None, netlist_tag=None):
    env = dict(os.environ, NETLIST=str(OUT / f"netlist_{netlist_tag or tag}.v"), PERIOD=str(period),
               LIBDIR=str(LIB), OUTDIR=str(OUT), TAG=tag, ACT=act or "", ACT_SCOPE=scope or "")
    p = subprocess.run(["sta", "-no_init", "-exit", str(ROOT / "syn" / "yosys" / "sta.tcl")],
                       env=env, capture_output=True, text=True)
    if p.returncode != 0 or "Error" in p.stdout:
        print(p.stdout[-3000:], p.stderr[-3000:])
        raise RuntimeError(f"OpenSTA failed ({tag})")
    vals = dict(l.split() for l in (OUT / f"timing_{tag}.txt").read_text().splitlines())
    return float(vals["wns_ps"]), float(vals["tns_ps"])


def area_by_block(tag):
    """Area per top-level block, submodules included, from Yosys `stat`
    (hierarchy kept). Local areas come from each module's exact
    'Chip area for module' line; the tree comes from the indented
    'design hierarchy' section. Returns ({block: um2}, fifo_um2, total_um2)."""
    txt = (OUT / f"area_{tag}.rpt").read_text()
    local = {m.group(1): float(m.group(2))
             for m in re.finditer(r"Chip area for module '\\?([^']+)': ([0-9.]+)", txt)}
    hier = txt[txt.index("=== design hierarchy ==="):].splitlines()
    nodes = []          # (depth, name)
    started = False
    for line in hier:
        # the tree ends at the first blank line after it starts; the cell
        # list that follows also names submodules and must not be parsed
        if started and not line.strip():
            break
        m = re.match(r"^\s+\d+\s+\S+ (\s*)(\S+)$", line)
        if m and m.group(2) in local:
            started = True
            nodes.append((len(m.group(1)) // 2, m.group(2)))
    blocks, fifo, stack = {}, 0.0, []
    for depth, name in nodes:
        a = local[name]
        if "xdma_fifo" in name.split("$")[0]:
            fifo += a
        if depth == 0 and name == "xdma_top":
            continue
        if depth == 0:
            blocks[name] = a
            stack = [name]
        else:
            blocks[stack[0]] += a
    total = sum(local[n] for _, n in nodes)
    pretty = {}
    for k, v in blocks.items():
        inst = k.split("$xdma_top.")[-1] if "$" in k else k
        pretty[inst] = round(v, 3)
    pretty["(top-level glue)"] = round(local.get("xdma_top", 0.0), 3)
    return pretty, fifo, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1000)
    ap.add_argument("--step", type=int, default=50)
    ap.add_argument("--period", type=int, default=None, help="skip the sweep, use this period")
    ap.add_argument("--no-power", action="store_true")
    ap.add_argument("--act-scope", default="xdma_tb/u_dut")
    ap.add_argument("--params", default="", help="K=V,.. parameter overrides (area experiments)")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if not (LIB / ".done").exists():
        subprocess.run([str(ROOT / "syn" / "yosys" / "fetch_asap7.sh")], check=True)
    params = dict(kv.split("=") for kv in filter(None, a.params.split(",")))
    pre = a.tag + "_" if a.tag else ""

    sweep = []
    if a.period:
        best = a.period
    else:
        best = None
        p = a.start
        while p > 0:
            tag = f"{pre}p{p}"
            synth(p, False, tag, params)
            wns, tns = sta(p, tag)
            sweep.append(dict(period_ps=p, wns_ps=wns, tns_ps=tns))
            print(f"period {p} ps: WNS {wns:.1f} ps, TNS {tns:.1f} ps", flush=True)
            if wns < 0:
                break
            best = p
            p -= a.step
        if best is None:
            raise SystemExit(f"no passing period at or below {a.start} ps")

    acts = {} if a.no_power else activity()
    summary = dict(flow="Yosys + OpenSTA, ASAP7 RVT TT NLDM, post-synthesis", params=params,
                   sweep=sweep, period_ps=best, fmax_ghz=1000.0 / best)
    for gate in (False, True):
        tag = f"{pre}final_{'cg' if gate else 'nocg'}"
        synth(best, gate, tag, params)
        wns, tns = sta(best, tag)
        areas, fifo, total = area_by_block(tag)
        res = dict(wns_ps=wns, tns_ps=tns, area_um2=total, fifo_area_um2=fifo,
                   fifo_share=fifo / total if total else None, area_by_block=areas)
        if not a.no_power:
            for kind, act in acts.items():
                ptag = f"{tag}_{kind}"
                sta(best, ptag, act=act, scope=a.act_scope, netlist_tag=tag)
                res[f"power_{kind}"] = parse_power(OUT / f"power_{ptag}.rpt")
                res[f"activity_{kind}"] = parse_activity(OUT / f"activity_{ptag}.rpt")
        summary["clock_gating" if gate else "no_clock_gating"] = res
        print(f"{tag}: area {total} um2, WNS {wns:.1f} ps", flush=True)
    with open(OUT / f"{pre}summary.json", "w") as f:
        json.dump(summary, f, indent=1)
    print(json.dumps({k: v for k, v in summary.items() if k != "sweep"}, indent=1)[:3000])
    return 0


def activity():
    """Switching activity for power: SAIF from RTL simulation (Verilator) of
    perf_saturate (all four channels saturating) and perf_idle, rewritten so
    instance names match the Yosys netlist."""
    acts = {}
    for kind, test, extra in (("active", "perf_saturate", {}),
                              ("idle", "perf_idle", {})):
        raw, flat = OUT / f"{kind}_raw.saif", OUT / f"{kind}.saif"
        env = dict(os.environ, XDMA_SAIF=str(raw), **extra)
        p = subprocess.run([sys.executable, str(ROOT / "tb" / "run.py"), "--test", test, "--seed", "1"],
                           env=env, capture_output=True, text=True)
        if "1/1 tests passed" not in p.stdout:
            print(p.stdout[-3000:])
            raise RuntimeError(f"activity simulation {test} failed")
        subprocess.run([sys.executable, str(ROOT / "syn" / "yosys" / "saif_flatten.py"), str(raw), str(flat)],
                       check=True)
        raw.unlink()
        acts[kind] = str(flat)
    return acts


def parse_activity(path):
    """Pins annotated from the SAIF vs. left to OpenSTA's propagation."""
    d = {}
    for line in path.read_text().splitlines():
        f = line.split()
        if len(f) == 2 and f[1].isdigit():
            d[f[0]] = int(f[1])
    return d


def parse_power(path):
    """Total internal / switching / leakage / total power (W) from OpenSTA."""
    for line in path.read_text().splitlines():
        f = line.split()
        if f and f[0] == "Total" and len(f) >= 5:
            return dict(internal_w=float(f[1]), switching_w=float(f[2]),
                        leakage_w=float(f[3]), total_w=float(f[4]))
    return None


if __name__ == "__main__":
    sys.exit(main())
