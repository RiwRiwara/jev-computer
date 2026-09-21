#!/usr/bin/env python3
"""
build_cpu.py — build the Jev computer as a pure-NAND netlist -> cpu.json.

The whole machine (8-bit CPU + 256 bytes of RAM) is one synchronous circuit:
    state bits (registers + all 2,048 RAM bits)  --NAND netlist-->  next state bits
Every gate is later answered by Jev; this file only wires them.

    python build_cpu.py              # write cpu.json
    python build_cpu.py --check      # verify cpu.json against the ISA (ideal gates, no API)
"""
import json
import random
import sys

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"  # pinned: the verified gate answers are only valid for this version
WORDS = 256           # bytes of RAM; addresses and the PC are 8-bit


# ───────────────────────────── netlist ─────────────────────────────

class Circuit:
    """A pure-NAND netlist. Nodes are ints; ('in',) or ('nand', x, y)."""

    def __init__(self):
        self.nodes = []
        self.level = []
        self.hash = {}

    def inp(self):
        self.nodes.append(("in",))
        self.level.append(0)
        return len(self.nodes) - 1

    def bus(self, width):  # LSB first
        return [self.inp() for _ in range(width)]

    def nand(self, x, y):
        key = (min(x, y), max(x, y))
        if key in self.hash:  # structural hashing: reuse identical gates
            return self.hash[key]
        self.nodes.append(("nand", x, y))
        self.level.append(1 + max(self.level[x], self.level[y]))
        self.hash[key] = len(self.nodes) - 1
        return self.hash[key]

    # everything below is built only from nand()
    def not_(self, x):      return self.nand(x, x)
    def and_(self, x, y):   return self.not_(self.nand(x, y))
    def or_(self, x, y):    return self.nand(self.not_(x), self.not_(y))

    def xor(self, x, y):
        t = self.nand(x, y)
        return self.nand(self.nand(x, t), self.nand(y, t))

    def mux(self, sel, one, zero):  # sel ? one : zero
        return self.nand(self.nand(sel, one), self.nand(self.not_(sel), zero))

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

    def increment(self, bits):  # bits + 1 with half adders (carry out dropped)
        out, carry = [], None
        for i, b in enumerate(bits):
            if i == 0:
                out.append(self.not_(b)); carry = b
            else:
                out.append(self.xor(b, carry)); carry = self.and_(b, carry)
        return out

    def cone(self, outputs):
        seen, stack = set(), list(outputs)
        while stack:
            n = stack.pop()
            if n not in seen:
                seen.add(n)
                if self.nodes[n][0] == "nand":
                    stack += [self.nodes[n][1], self.nodes[n][2]]
        return seen


# ───────────────────────────── the CPU ─────────────────────────────
#
# 8-bit accumulator machine, 256 bytes of RAM.
# Every instruction is 2 bytes: [opcode] [operand]. The PC advances by 2.

OPS = {"NOP": 0, "LDA": 1, "ADD": 2, "SUB": 3, "STA": 4, "LDI": 5,
       "JMP": 6, "JZ": 7, "JC": 8, "OUT": 9, "HLT": 15}


def cpu_core(c, pc, A, C, op, arg, mdr):
    """CPU logic: op/arg are the instruction bytes, mdr = RAM[arg]. Returns next-state buses."""
    opbits = op[:4]

    # ── instruction decoder (4→16) ──
    nop = [c.not_(b) for b in opbits]
    d = {name: c.and_many([opbits[i] if (code >> i) & 1 else nop[i] for i in range(4)])
         for name, code in OPS.items()}

    # ── ALU: A + (MDR xor sub) + sub ──
    sub = d["SUB"]
    carry, s = sub, []
    for i in range(8):
        bit, carry = c.full_adder(A[i], c.xor(mdr[i], sub), carry)
        s.append(bit)
    alu = c.or_(d["ADD"], d["SUB"])

    zero = c.not_(c.or_many(A))  # zero flag of current A

    # ── next A: one-hot select between MDR / ALU / immediate / keep ──
    keep = c.not_(c.or_many([d["LDA"], alu, d["LDI"]]))
    next_a = [c.or_many([c.and_(d["LDA"], mdr[i]), c.and_(alu, s[i]),
                         c.and_(d["LDI"], arg[i]), c.and_(keep, A[i])]) for i in range(8)]

    next_c = c.mux(alu, carry, C)  # carry flag register input

    # ── program counter: PC + 2, or jump to the operand ──
    plus2 = [pc[0]] + c.increment(pc[1:])
    jump = c.or_many([d["JMP"], c.and_(d["JZ"], zero), c.and_(d["JC"], C)])
    next_pc = [c.mux(jump, arg[i], plus2[i]) for i in range(8)]

    return {"pc": next_pc, "a": next_a, "c": [next_c],
            "halt": d["HLT"], "out": d["OUT"], "store": d["STA"]}


def reference_step(ram, pc, a, c):
    """Plain-Python ISA semantics, used only to check the netlist."""
    op, x = ram[pc] & 15, ram[(pc + 1) % WORDS]
    m = ram[x]
    npc, na, nc = (pc + 2) % WORDS, a, c
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
    """8-bit address -> 256 one-hot selects, via two 4→16 predecoders."""
    def dec4(bits):
        inv = [c.not_(b) for b in bits]
        return [c.and_many([bits[i] if (k >> i) & 1 else inv[i] for i in range(4)])
                for k in range(16)]
    lo, hi = dec4(addr[:4]), dec4(addr[4:])
    return [c.and_(hi[k >> 4], lo[k & 15]) for k in range(WORDS)]


def read(c, sel, ram):
    """RAM read port: OR over words of (select AND bit), as NOT(AND of NANDs)."""
    return [c.not_(c.and_many([c.nand(sel[k], ram[k][b]) for k in range(WORDS)]))
            for b in range(8)]


def build():
    c = Circuit()
    regs = {"pc": c.bus(8), "a": c.bus(8), "c": [c.inp()]}
    ram = [c.bus(8) for _ in range(WORDS)]
    for k in range(WORDS):
        regs[f"ram{k}"] = ram[k]

    pc = regs["pc"]
    op = read(c, decoder(c, pc), ram)                   # instruction byte
    arg = read(c, decoder(c, c.increment(pc)), ram)     # operand byte
    arg_sel = decoder(c, arg)
    mdr = read(c, arg_sel, ram)                         # RAM[operand]
    o = cpu_core(c, pc, regs["a"], regs["c"][0], op, arg, mdr)

    nxt = {"pc": o["pc"], "a": o["a"], "c": o["c"]}
    for k in range(WORDS):                              # RAM write-back (STA)
        we = c.and_(o["store"], arg_sel[k])
        nxt[f"ram{k}"] = [c.mux(we, regs["a"][b], ram[k][b]) for b in range(8)]

    # keep only what the outputs need; number state bits first, then gates by level
    names = list(regs)
    state = [n for r in names for n in regs[r]]
    cone = c.cone([n for r in names for n in nxt[r]] + [o["halt"], o["out"]])
    gates = sorted((n for n in cone if c.nodes[n][0] == "nand"), key=lambda n: (c.level[n], n))
    ids = {n: i for i, n in enumerate(state)}
    ids.update({n: len(state) + i for i, n in enumerate(gates)})

    return {
        "about": "8-bit computer with 256 bytes of RAM; every gate is a Jev Noul (AND), inverted to NAND.",
        "api": API_URL,
        "model": MODEL,
        # one Noul per gate case, packed into one request; {id} names the case in state
        "gate": {"instructions": "Are both `{id}.a` and `{id}.b` equal to 1?", "bit_if_yes": 0},
        "registers": [[r, len(regs[r])] for r in names],
        "gates": [[ids[c.nodes[n][1]], ids[c.nodes[n][2]]] for n in gates],
        "levels": max(c.level[n] for n in gates),
        "next": [ids[n] for r in names for n in nxt[r]],
        "halt": ids[o["halt"]],
        "display": {"when": ids[o["out"]], "show": [ids[n] for n in regs["a"]]},
        # how program JSON is assembled: 2 bytes per instruction, [opcode] [operand]
        "isa": {"opcodes": OPS, "instruction_bytes": 2, "memory": "ram", "words": WORDS},
    }


def check(m, n):
    """One clock tick of cpu.json (ideal NAND) vs reference_step, on n random states."""
    rng = random.Random(1)
    for _ in range(n):
        ram = [rng.randrange(256) for _ in range(WORDS)]
        pc, a, cf = rng.randrange(WORDS), rng.randrange(256), rng.randrange(2)
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
            want_ram[ram[(pc + 1) % WORDS]] = a
        got = {"pc": nxt["pc"], "a": nxt["a"], "c": nxt["c"], "halt": v[m["halt"]],
               "out": v[m["display"]["when"]], "ram": [nxt[f"ram{k}"] for k in range(WORDS)]}
        want = {k: want[k] for k in ("pc", "a", "c", "halt", "out")} | {"ram": want_ram}
        if got != want:
            print(f"MISMATCH pc={pc} a={a} c={cf} op={ram[pc]} arg={ram[(pc + 1) % WORDS]}")
            return False
    print(f"cpu.json OK — {n} random machine states match the reference ISA")
    return True


if __name__ == "__main__":
    if "--check" in sys.argv:
        with open("cpu.json") as f:
            sys.exit(0 if check(json.load(f), 300) else 1)
    m = build()
    with open("cpu.json", "w") as f:
        json.dump(m, f, separators=(",", ":"))
    print(f"cpu.json: {len(m['gates']):,} gates, {m['levels']} levels, "
          f"{len(m['next']):,} state bits")
