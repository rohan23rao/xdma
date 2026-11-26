#!/usr/bin/env python3
"""Rewrite a Verilator SAIF so its instance names match the Yosys netlist.

Verilator nests generate blocks as their own SAIF instances
(xdma_tb/u_dut/g_ch[0]/g_mm2s/u_rd/...), while the hierarchical Yosys
netlist names the same instance after the whole generate path
(g_ch[0].g_mm2s.u_rd). This script merges every instance whose name starts
with "g_" into the names of its children, joined with '.', and drops the
assertion-module instances (u_sva), which have no hardware, and the
Verilator-only $rootio and package scopes.

    python3 syn/yosys/saif_flatten.py in.saif out.saif
"""
import re, sys

TOKEN = re.compile(r"\(|\)|[^\s()]+")


def parse(text):
    stack, cur = [], []
    for t in TOKEN.findall(text):
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done = cur
            cur = stack.pop()
            cur.append(done)
        else:
            cur.append(t)
    return cur


def rewrite(node, prefix=""):
    """Returns a list of nodes replacing `node` (generate scopes expand to
    their children, renamed)."""
    if not isinstance(node, list):
        return [node]
    if not node or node[0] != "INSTANCE":
        body = []
        for child in node:
            body += rewrite(child)
        return [body]
    name = node[1]
    # assertion modules have no hardware; Verilator's $rootio and package
    # scopes are not part of the design (and '$' breaks OpenSTA's parser)
    if name == "u_sva" or name.startswith("$") or name == "xdma_pkg":
        return []
    if name.startswith("g_"):
        out = []
        for child in node[2:]:
            if isinstance(child, list) and child and child[0] == "INSTANCE":
                child = [child[0], f"{prefix}{name}.{child[1]}"] + child[2:]
                out += rewrite(child, "")
        return out
    body = []
    for child in node[2:]:
        body += rewrite(child)
    return [[node[0], name] + body]


def emit(node, out, depth=0):
    if isinstance(node, list):
        out.append("(" + " ".join(x for x in node if not isinstance(x, list)))
        for x in node:
            if isinstance(x, list):
                emit(x, out, depth + 1)
        out.append(")")
    else:
        out.append(node)


def main():
    src, dst = sys.argv[1], sys.argv[2]
    text = "\n".join(l for l in open(src).read().splitlines() if not l.lstrip().startswith("//"))
    tree = parse(text)
    out = []
    for top in tree:
        for n in rewrite(top):
            emit(n, out)
    with open(dst, "w") as f:
        f.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
