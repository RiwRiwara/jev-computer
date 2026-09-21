#!/usr/bin/env python3
"""
jev_computer.py — an 8-bit computer whose entire CPU is a NAND netlist, and
every NAND gate is answered by Jev (TypeSafe System One model).

What Jev does (all the logic):
    instruction decoder, ALU (add/sub), carry, zero flag, PC incrementer,
    jump mux, register-select muxes  ->  340 NAND gates (34 levels deep), evaluated by Jev.

What Python does (only the parts with no logic in real hardware):
    wires, register latches (flip-flops), 16-byte RAM cells, the clock.

Gate primitive (verified over its full input space by --selftest):
    Noul "Are both a and b equal to 1?"  ->  AND,  then an inverter  ->  NAND.
    (Asking the negated question directly is worse for Noul; NOT is a wire-level
    inverter in hardware too, and AND+NOT is exactly as universal as NAND.)

Modes:
    --mode table   (default) Jev is asked the 4 gate cases at boot, the answers
                   are frozen into a truth table, the CPU then runs on it.
    --mode live    Every circuit level on every clock cycle re-asks Jev for the
                   input patterns present at that level. Slow, but Jev is truly
                   in the loop for every gate evaluation.
    --sim          Ideal gates, no API. For checking the netlist offline.

Usage:
    export TYPESAFE_API_KEY="..."
    python jev_computer.py --selftest
    python jev_computer.py fib
    python jev_computer.py countdown --mode live
    python jev_computer.py --check --sim        # verify netlist vs reference
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

def load_env():  # read KEY=VALUE lines from .env next to this script (no dependency)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        for line in open(path):
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k.removeprefix("export ").strip(), v.strip().strip("'\""))
load_env()

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"  # pinned: the verified truth table is only valid for this version

GATE_QUESTION = {
    "type": "noul",
    "instructions": "Are both `a` and `b` equal to 1?",
    "criteria": {
        "true": "a is 1 and b is 1",
        "false": "at least one of a or b is 0",
    },
}
WEAK_MARGIN = 0.15  # warn when |p - 0.5| < this (p within 0.35..0.65): bit not sharp


# ───────────────────────────── gate backends ─────────────────────────────

class JevGate:
    """Asks Jev one AND question per input pattern; returns NAND bits."""

    def __init__(self, api_key, workers=4):
        self.api_key = api_key
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.requests = 0
        self.weak = []  # (a, b, p) answers that were close to 0.5

    def ask_and(self, a, b):
        body = json.dumps({
            "model": MODEL,
            "state": {"a": a, "b": b},
            "questions": {"and": GATE_QUESTION},
        }).encode()
        for attempt in range(6):
            req = urllib.request.Request(API_URL, data=body, headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            })
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    self.requests += 1
                    p = json.load(r)["answers"]["and"]["noul"]
                    if abs(p - 0.5) < WEAK_MARGIN:
                        self.weak.append((a, b, p))
                    return p
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    wait = float(e.headers.get("retry-after") or 2 ** attempt)
                    time.sleep(min(wait, 30))
                    continue
                raise SystemExit(f"API error {e.code}: {e.read().decode()[:300]}")
            except urllib.error.URLError:
                time.sleep(2 ** attempt)
        raise SystemExit("API unreachable after retries")

    def nand_patterns(self, patterns):
        """patterns: iterable of distinct (a, b). Returns {(a, b): nand_bit}."""
        patterns = list(patterns)
        probs = list(self.pool.map(lambda ab: self.ask_and(*ab), patterns))
        return {ab: 0 if p >= 0.5 else 1 for ab, p in zip(patterns, probs)}


class TableGate:
    """Truth table learned from Jev once (or ideal, for --sim)."""

    def __init__(self, table):
        self.table = table
        self.requests = 0

    def nand_patterns(self, patterns):
        return {ab: self.table[ab] for ab in patterns}


IDEAL = {(0, 0): 1, (0, 1): 1, (1, 0): 1, (1, 1): 0}


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

    def evaluate(self, values, gate, outputs):
        """Evaluate level by level; each level is one round of Jev calls."""
        val = {self.inputs[k]: v for k, v in values.items()}
        needed = self._cone(outputs)
        by_level = {}
        for n in needed:
            if self.nodes[n][0] == "nand":
                by_level.setdefault(self.level[n], []).append(n)
        rounds = 0
        for lv in sorted(by_level):
            gates = by_level[lv]
            pats = {(val[self.nodes[n][1]], val[self.nodes[n][2]]) for n in gates}
            answer = gate.nand_patterns(pats)
            rounds += 1
            for n in gates:
                _, x, y = self.nodes[n]
                val[n] = answer[(val[x], val[y])]
        return val, rounds, sum(len(g) for g in by_level.values())

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
OPNAME = {v: k for k, v in OPS.items()}


def build_cpu():
    """One combinational circuit = one clock tick of the whole CPU."""
    c = Circuit()
    pc = c.bus("pc", 4)
    A = c.bus("a", 8)
    C = c.inp("c")
    ir = c.bus("ir", 8)
    mdr = c.bus("mdr", 8)          # RAM[ir.addr], read by the memory bus
    return c, cpu_core(c, pc, A, C, ir, mdr)


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


def to_bits(prefix, value, width):
    return {f"{prefix}{i}": (value >> i) & 1 for i in range(width)}


def from_bits(val, nodes):
    return sum(val[n] << i for i, n in enumerate(nodes))


class Computer:
    def __init__(self, gate):
        self.cpu, self.outs = build_cpu()
        self.all_out = [n for ns in self.outs.values() for n in ns]
        self.gate = gate

    def tick(self, ram, pc, a, c):
        ir = ram[pc]
        values = {**to_bits("pc", pc, 4), **to_bits("a", a, 8), "c": c,
                  **to_bits("ir", ir, 8), **to_bits("mdr", ram[ir & 15], 8)}
        val, rounds, gates = self.cpu.evaluate(values, self.gate, self.all_out)
        o = {k: from_bits(val, ns) for k, ns in self.outs.items()}
        return o, rounds, gates

    def run(self, ram, max_cycles=500, trace=True):
        ram = list(ram)
        pc, a, c = 0, 0, 0
        printed = []
        for cycle in range(max_cycles):
            ir = ram[pc]
            t0 = time.time()
            o, rounds, gates = self.tick(ram, pc, a, c)
            if trace:
                op = OPNAME.get(ir >> 4, "?")
                print(f"  {cycle:3d}  pc={pc:2d}  {op:<3} {ir & 15:2d}   "
                      f"A={a:3d} C={c}  │ {gates} gates, {rounds} levels, "
                      f"{time.time() - t0:5.2f}s")
            if o["halt"]:
                break
            if o["out"]:
                printed.append(a)
                print(f"  ▶ OUT {a}")
            if o["store"]:
                ram[ir & 15] = a          # RAM write — a latch, not logic
            pc, a, c = o["pc"], o["a"], o["c"]  # clock edge: latch registers
        return printed, ram


# ───────────────────────────── programs ─────────────────────────────

def asm(program, data=None):
    ram = [0] * 16
    for i, line in enumerate(program):
        op, *arg = line.split()
        ram[i] = (OPS[op] << 4) | (int(arg[0]) if arg else 0)
    for addr, v in (data or {}).items():
        ram[addr] = v
    return ram


PROGRAMS = {
    # Fibonacci until the 8-bit adder overflows: 0 1 1 2 3 5 ... 144
    "fib": asm(["LDA 13", "OUT", "ADD 14", "JC 10", "STA 15",
                "LDA 14", "STA 13", "LDA 15", "STA 14", "JMP 0", "HLT"],
               {13: 0, 14: 1}),
    # 5 4 3 2 1 0
    "countdown": asm(["LDI 5", "OUT", "JZ 5", "SUB 15", "JMP 1", "HLT"], {15: 1}),
    # 7 * 6 by repeated addition -> 42
    "mul": asm(["LDA 13", "ADD 14", "STA 13", "LDA 15", "SUB 12", "STA 15",
                "JZ 8", "JMP 0", "LDA 13", "OUT", "HLT"],
               {12: 1, 13: 0, 14: 7, 15: 6}),
}


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


# ───────────────────────────── entry points ─────────────────────────────

def selftest(api_key, repeats):
    """Exhaustive: all 4 gate inputs, several times each (catches flaky bits)."""
    g = JevGate(api_key)
    print(f"Gate selftest — {MODEL}, {repeats}x each of 4 cases\n")
    print("   a b   want NAND   p(AND) per run              result")
    table, ok = {}, True
    for ab in [(0, 0), (0, 1), (1, 0), (1, 1)]:
        ps = list(g.pool.map(lambda _: g.ask_and(*ab), range(repeats)))
        bits = {0 if p >= 0.5 else 1 for p in ps}
        want = IDEAL[ab]
        worst = min(abs(p - 0.5) for p in ps)
        good = bits == {want}
        ok &= good
        table[ab] = want if good else None
        flag = "PASS" if good else "FAIL"
        if good and worst < 0.4:  # some run had p between 0.1 and 0.9
            flag += " (weak)"
        print(f"   {ab[0]} {ab[1]}     {want}       "
              f"{' '.join(f'{p:.2f}' for p in ps):<26}  {flag}")
    print(f"\n{'ALL PASS — gate is verified over its full input space' if ok else 'FAILED'}"
          f"  ({g.requests} requests)")
    return table if ok else None


def check_netlist(gate, n):
    comp = Computer(gate)
    rng = random.Random(1)
    for _ in range(n):
        ram = [rng.randrange(256) for _ in range(16)]
        pc, a, c = rng.randrange(16), rng.randrange(256), rng.randrange(2)
        got, _, _ = comp.tick(ram, pc, a, c)
        want = reference_step(ram, pc, a, c)
        if got != want:
            print(f"MISMATCH pc={pc} a={a} c={c} ir={ram[pc]:08b}\n  got  {got}\n  want {want}")
            return False
    print(f"netlist OK — {n} random CPU states match the reference ISA")
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("program", nargs="?", choices=list(PROGRAMS))
    ap.add_argument("--mode", choices=["table", "live"], default="table")
    ap.add_argument("--sim", action="store_true", help="ideal gates, no API calls")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--check", type=int, nargs="?", const=2000, metavar="N",
                    help="verify netlist against reference ISA on N random states")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    cpu, _ = build_cpu()
    n_gates = sum(1 for x in cpu.nodes if x[0] == "nand")
    depth = max(cpu.level)
    print(f"Jev CPU: {n_gates} NAND gates, {depth} levels deep\n")

    key = os.environ.get("TYPESAFE_API_KEY")
    if args.sim:
        gate = TableGate(IDEAL)
    elif not key:
        sys.exit("set TYPESAFE_API_KEY, or use --sim for ideal gates")
    elif args.selftest:
        sys.exit(0 if selftest(key, args.repeats) else 1)
    elif args.mode == "live":
        gate = JevGate(key)
    else:
        table = selftest(key, args.repeats)
        if not table:
            sys.exit("gate did not pass — refusing to boot on an unverified primitive")
        gate = TableGate(table)
        print()

    if args.check:
        sys.exit(0 if check_netlist(gate, args.check) else 1)
    if not args.program:
        ap.error("choose a program: " + ", ".join(PROGRAMS))

    print(f"running '{args.program}' ({'ideal gates' if args.sim else 'Jev ' + args.mode})")
    t0 = time.time()
    printed, _ = Computer(gate).run(PROGRAMS[args.program], trace=not args.quiet)
    print(f"\noutput: {printed}")
    reqs = getattr(gate, "requests", 0)
    print(f"{time.time() - t0:.1f}s" + (f", {reqs} Jev requests in run" if reqs else ""))
    weak = getattr(gate, "weak", [])
    if weak:
        print(f"⚠ {len(weak)} gate answers were not sharp (p within 0.35–0.65): {weak[:5]}")


if __name__ == "__main__":
    main()
