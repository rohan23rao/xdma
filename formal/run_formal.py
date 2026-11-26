#!/usr/bin/env python3
"""Run every SymbiYosys target in formal/sby and summarize the results.

Usage: python3 formal/run_formal.py [target ...]
Writes formal/out/summary.md and formal/out/summary.csv. Targets run one at a
time (some proofs need several GB of memory). Exit status is non-zero if any
task does not PASS.
"""
import csv, pathlib, re, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parent
SBY_DIR = ROOT / "sby"
OUT = ROOT / "out"


def tasks_of(sby):
    text = sby.read_text()
    m = re.search(r"\[tasks\]\n(.*?)\n\n", text, re.S)
    if not m:
        return [None]
    return [ln.split()[0] for ln in m.group(1).splitlines() if ln.strip() and not ln.startswith("#")]


def depth_of(sby, task):
    """Depth used for a task: from its [options] or sby default 20."""
    text = sby.read_text()
    kind = "cover" if task and task.startswith("cover") else ("bmc" if task and task.startswith("bmc") else "prove")
    m = re.search(rf"^{kind}: depth (\d+)", text, re.M)
    return int(m.group(1)) if m else 20


def main():
    want = sys.argv[1:]
    OUT.mkdir(exist_ok=True)
    rows = []
    for sby in sorted(SBY_DIR.glob("*.sby")):
        if want and sby.stem not in want:
            continue
        for task in tasks_of(sby):
            name = sby.stem + (f"_{task}" if task else "")
            t0 = time.time()
            cmd = ["sby", "-f", sby.name] + ([task] if task else [])
            proc = subprocess.run(cmd, cwd=SBY_DIR, capture_output=True, text=True)
            dt = time.time() - t0
            status_file = SBY_DIR / name / "status"
            status = status_file.read_text().split()[0] if status_file.exists() else "ERROR"
            kind = "cover" if task and task.startswith("cover") else ("bmc" if task and task.startswith("bmc") else "prove")
            if kind == "prove":
                result = "proven" if status == "PASS" else status.lower()
            elif kind == "bmc":
                result = f"bounded, depth {depth_of(sby, task)}" if status == "PASS" else status.lower()
            else:
                result = "all covers reached" if status == "PASS" else status.lower()
            fails = sorted(set(re.findall(r"failed assertion \S+\.(\S+?) ", proc.stdout)))
            unreached = sorted(set(re.findall(r"unreached cover statement \S+\.(\S+?) ", proc.stdout)))
            rows.append(dict(target=sby.stem, task=task or "-", kind=kind, status=status,
                             result=result, seconds=f"{dt:.1f}",
                             detail=" ".join(fails + unreached)))
            print(f"{name:32s} {status:8s} {result:28s} {dt:7.1f}s {' '.join(fails + unreached)}", flush=True)
    if want:
        return 0 if all(r["status"] == "PASS" for r in rows) else 1
    with open(OUT / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    with open(OUT / "summary.md", "w") as f:
        f.write("| Target | Task | Kind | Result | Time (s) |\n|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['target']} | {r['task']} | {r['kind']} | {r['result']} | {r['seconds']} |\n")
        npass = sum(r["status"] == "PASS" for r in rows)
        f.write(f"\n{npass}/{len(rows)} tasks pass. Produced by `make formal`.\n")
    return 0 if all(r["status"] == "PASS" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
