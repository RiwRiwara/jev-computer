#!/usr/bin/env python3
"""
build_cpu.py — build the Jev computer as a pure-NAND netlist -> cpu.json.

The whole machine (8-bit CPU + 16 bytes of RAM) is one synchronous circuit:
    state bits (registers + all 128 RAM bits)  --NAND netlist-->  next state bits
Every gate is later answered by Jev; this file only wires them.

    python build_cpu.py              # write cpu.json
    python build_cpu.py --check      # verify cpu.json against the ISA (ideal gates, no API)
"""
import json
import random
import sys

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"  # pinned: the verified gate answers are only valid for this version
WORDS = 16


# ───────────────────────────── netlist ─────────────────────────────

class Circuit:
    """A pure-NAND netlist. Nodes are ints; ('in', name) or ('nand', x, y)."""

    def __init__(self):
        self.nodes = []
        self.level = []
        self.inputs = {}
        self.hash = {}

    def inp(self, name):
        n = len(self.nodes)
        self.nodes.append(("in", name))
        self.level.append(0)
        self.inputs[name] = n
        return n

    def bus(self, name, width):  # LSB first
        return [self.inp(f"{name}{i}") for i in range(width)]

    def nand(self, x, y):
        key = (min(x, y), max(x, y))
        if key in self.hash:  # structural hashing: reuse identical gates
            return self.hash[key]
        n = len(self.nodes)
        self.nodes.append(("nand", x, y))
        self.level.append(1 + max(self.level[x], self.level[y]))
        self.hash[key] = n
        return n

    # everything below is built only from nand()
    def not_(self, x):      return self.nand(x, x)
    def and_(self, x, y):   return self.not_(self.nand(x, y))
    def or_(self, x, y):    return self.nand(self.not_(x), self.not_(y))
    def nor(self, x, y):    return self.not_(self.or_(x, y))

    def xor(self, x, y):
        t = self.nand(x, y)
        return self.nand(self.nand(x, t), self.nand(y, t))

    def and_many(self, xs):
        while len(xs) > 1:  # balanced tree keeps depth low
            xs = [self.and_(xs[i], xs[i + 1]) if i + 1 < len(xs) else xs[i]
                  for i in range(0, len(xs), 2)]
        return xs[0]

    def or_many(self, xs):
        while len(xs) > 1:
            xs = [self.or_(xs[i], xs[i + 1]) if i + 1 < len(xs) else xs[i]
                  for i in range(0, len(xs), 2)]
        return xs[0]

    def full_adder(self, a, b, cin):  # the classic 9-NAND full adder
        t1 = self.nand(a, b)
        t2 = self.nand(a, t1)
        t3 = self.nand(b, t1)
        s1 = self.nand(t2, t3)          # a xor b
        t4 = self.nand(s1, cin)
        t5 = self.nand(s1, t4)
        t6 = self.nand(cin, t4)
        s = self.nand(t5, t6)           # sum
        cout = self.nand(t1, t4)        # carry
        return s, cout

    def _cone(self, outputs):
        seen, stack = set(), list(outputs)
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            if self.nodes[n][0] == "nand":
                stack += [self.nodes[n][1], self.nodes[n][2]]
        return seen


# ───────────────────────────── the CPU ─────────────────────────────
#
# 8-bit accumulator machine, 16 bytes of RAM, instruction = [op:4][addr:4]

OPS = {"NOP": 0, "LDA": 1, "ADD": 2, "SUB": 3, "STA": 4, "LDI": 5,
       "JMP": 6, "JZ": 7, "JC": 8, "OUT": 9, "HLT": 15}


def cpu_core(c, pc, A, C, ir, mdr):
    """CPU logic on existing buses; returns the next-state/control buses."""
    addr, opbits = ir[:4], ir[4:]

    # ── instruction decoder (4→16), all gates ──
    nop = [c.not_(b) for b in opbits]
    def is_op(code):
        return c.and_many([opbits[i] if (code >> i) & 1 else nop[i] for i in range(4)])
    d = {name: is_op(code) for name, code in OPS.items()}

    # ── ALU: A + (MDR xor sub) + sub ──
    sub = d["SUB"]
    carry = sub
    s = []
    for i in range(8):
        bit, carry = c.full_adder(A[i], c.xor(mdr[i], sub), carry)
        s.append(bit)
    alu = c.or_(d["ADD"], d["SUB"])

    # ── zero flag of current A ──
    zero = c.not_(c.or_many(A))

    # ── next A: one-hot select between MDR / ALU / immediate / keep ──
    load = c.or_many([d["LDA"], alu, d["LDI"]])
    keep = c.not_(load)
    next_a = []
    for i in range(8):
        imm = addr[i] if i < 4 else None
        terms = [c.and_(d["LDA"], mdr[i]), c.and_(alu, s[i]), c.and_(keep, A[i])]
        if imm is not None:
            terms.append(c.and_(d["LDI"], imm))
        next_a.append(c.or_many(terms))

    # ── carry flag register input ──
    next_c = c.or_(c.and_(alu, carry), c.and_(c.not_(alu), C))

    # ── program counter: PC+1 via half adders, then jump mux ──
    inc, k = [], None
    for i in range(4):
        if i == 0:
            inc.append(c.not_(pc[0])); k = pc[0]
        else:
            inc.append(c.xor(pc[i], k)); k = c.and_(pc[i], k)
    jump = c.or_many([d["JMP"], c.and_(d["JZ"], zero), c.and_(d["JC"], C)])
    njump = c.not_(jump)
    next_pc = [c.nand(c.nand(jump, addr[i]), c.nand(njump, inc[i])) for i in range(4)]

    outs = {"pc": next_pc, "a": next_a, "c": [next_c],
            "halt": [d["HLT"]], "out": [d["OUT"]], "store": [d["STA"]]}
    return outs


def reference_step(ram, pc, a, c):
    """Plain-Python ISA semantics, used only to check the netlist."""
    ir = ram[pc]; op, x = ir >> 4, ir & 15; m = ram[x]
    npc, na, nc = (pc + 1) & 15, a, c
    if op == 1: na = m
    elif op == 2: t = a + m; na, nc = t & 255, t >> 8
    elif op == 3: t = a + (m ^ 255) + 1; na, nc = t & 255, t >> 8
    elif op == 5: na = x
    elif op == 6: npc = x
    elif op == 7 and a == 0: npc = x
    elif op == 8 and c: npc = x
    return {"pc": npc, "a": na, "c": nc, "halt": int(op == 15),
            "out": int(op == 9), "store": int(op == 4)}


# ───────────────────────────── whole machine ─────────────────────────────

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
        # how program JSON is assembled: word = opcode << operand_bits | operand
        "isa": {"opcodes": OPS, "operand_bits": 4, "memory": "ram", "words": WORDS},
    }


def check(m, n):
    """One clock tick of cpu.json (ideal NAND) vs reference_step, on n random states."""
    rng = random.Random(1)
    for _ in range(n):
        ram = [rng.randrange(256) for _ in range(WORDS)]
        pc, a, cf = rng.randrange(16), rng.randrange(256), rng.randrange(2)
        init = {"pc": pc, "a": a, "c": cf, **{f"ram{k}": v for k, v in enumerate(ram)}}
        v = [(init[r] >> i) & 1 for r, w in m["registers"] for i in range(w)]
        for x, y in m["gates"]:
            v.append(1 - (v[x] & v[y]))
        nxt, i = {}, 0
        for r, w in m["registers"]:
            nxt[r] = sum(v[m["next"][i + j]] << j for j in range(w)); i += w
        want = reference_step(ram, pc, a, cf)
        want_ram = list(ram)
        if want["store"]:
            want_ram[ram[pc] & 15] = a
        got = {"pc": nxt["pc"], "a": nxt["a"], "c": nxt["c"], "halt": v[m["halt"]],
               "out": v[m["display"]["when"]], "ram": [nxt[f"ram{k}"] for k in range(WORDS)]}
        want = {k: want[k] for k in ("pc", "a", "c", "halt", "out")} | {"ram": want_ram}
        if got != want:
            print(f"MISMATCH pc={pc} a={a} c={cf} ir={ram[pc]:08b}\n  got  {got}\n  want {want}")
            return False
    print(f"cpu.json OK — {n} random machine states match the reference ISA")
    return True


if __name__ == "__main__":
    if "--check" in sys.argv:
        with open("cpu.json") as f:
            sys.exit(0 if check(json.load(f), 2000) else 1)
    m = build()
    with open("cpu.json", "w") as f:
        json.dump(m, f, separators=(",", ":"))
    print(f"cpu.json: {len(m['gates'])} gates, {len(m['level_sizes'])} levels, "
          f"{len(m['next'])} state bits")
