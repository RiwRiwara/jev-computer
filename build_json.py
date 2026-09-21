#!/usr/bin/env python3
"""
build_json.py — compile the whole machine (CPU + RAM) into cpu.json, and each
program into programs/<name>.json. Run once; jev_run.py then needs only JSON.

In cpu.json the entire machine is one synchronous circuit:
    state bits (registers + all 128 RAM bits)  --NAND netlist-->  next state bits
RAM reads and writes are gates too (address decoders + muxes), so the runner
knows nothing about CPUs: it only holds bits, asks Jev per gate, and ticks.
"""
import json
import os

from jev_computer import API_URL, MODEL, PROGRAMS, Circuit, cpu_core

WORDS = 16


def decoder(c, addr):
    inv = [c.not_(b) for b in addr]
    return [c.and_many([addr[i] if (k >> i) & 1 else inv[i] for i in range(4)])
            for k in range(WORDS)]


def read(c, sel, ram):
    return [c.or_many([c.and_(sel[k], ram[k][b]) for k in range(WORDS)]) for b in range(8)]


def build():
    c = Circuit()
    regs = {"pc": c.bus("pc", 4), "a": c.bus("a", 8), "c": [c.inp("c")]}
    ram = [c.bus(f"ram{k}_", 8) for k in range(WORDS)]
    for k in range(WORDS):
        regs[f"ram{k}"] = ram[k]

    ir = read(c, decoder(c, regs["pc"]), ram)          # instruction fetch
    addr_sel = decoder(c, ir[:4])
    mdr = read(c, addr_sel, ram)                        # operand fetch
    o = cpu_core(c, regs["pc"], regs["a"], regs["c"][0], ir, mdr)

    nxt = {"pc": o["pc"], "a": o["a"], "c": o["c"]}
    store = o["store"][0]
    for k in range(WORDS):                              # RAM write-back
        we = c.and_(store, addr_sel[k])
        nwe = c.not_(we)
        nxt[f"ram{k}"] = [c.nand(c.nand(we, regs["a"][b]), c.nand(nwe, ram[k][b]))
                          for b in range(8)]

    # cone of everything we need, renumbered: state bits first, gates by level
    names = list(regs)
    state_nodes = [n for r in names for n in regs[r]]
    roots = [n for r in names for n in nxt[r]] + [o["halt"][0], o["out"][0]]
    cone = c._cone(roots)
    gates = sorted((n for n in cone if c.nodes[n][0] == "nand"), key=lambda n: (c.level[n], n))
    ids = {n: i for i, n in enumerate(state_nodes)}
    ids.update({n: len(state_nodes) + i for i, n in enumerate(gates)})

    levels = []
    for n in gates:
        if not levels or c.level[n] != levels[-1][0]:
            levels.append([c.level[n], 0])
        levels[-1][1] += 1

    return {
        "about": "8-bit computer; every gate is a Jev Noul (AND), inverted to NAND.",
        "api": API_URL,
        "model": MODEL,
        # one Noul per gate case, packed into one request; {id} names the case in state
        "gate": {"instructions": "Are both `{id}.a` and `{id}.b` equal to 1?", "bit_if_yes": 0},
        "registers": [[r, len(regs[r])] for r in names],
        "gates": [[ids[c.nodes[n][1]], ids[c.nodes[n][2]]] for n in gates],
        "level_sizes": [size for _, size in levels],
        "next": [ids[n] for r in names for n in nxt[r]],
        "halt": ids[o["halt"][0]],
        "display": {"when": ids[o["out"][0]], "show": [ids[n] for n in regs["a"]]},
        "trace": ["pc", "a"],
    }


if __name__ == "__main__":
    m = build()
    with open("cpu.json", "w") as f:
        json.dump(m, f, separators=(",", ":"))
    os.makedirs("programs", exist_ok=True)
    for name, image in PROGRAMS.items():
        with open(f"programs/{name}.json", "w") as f:
            json.dump({f"ram{k}": v for k, v in enumerate(image) if v}, f, indent=1)
    print(f"cpu.json: {len(m['gates'])} gates, {len(m['level_sizes'])} levels, "
          f"{len(m['next'])} state bits; programs: {', '.join(PROGRAMS)}")
