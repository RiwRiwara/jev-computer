#!/usr/bin/env python3
"""
jev_run.py — run a program on the Jev computer.

The machine is cpu.json (a NAND netlist); every gate is answered by Jev.
Programs are JSON files in programs/ (see README for the format).

    python jev_run.py programs/fib.json                 # run it (1 Jev request)
    python jev_run.py programs/add.json a=40 b=3        # set the program's inputs
    python jev_run.py programs/add.json a=40 b=3 --trace   # show every instruction
    python jev_run.py programs/add.json --test          # run the tests inside the JSON
    python jev_run.py --selftest                        # check the gate answers, 5 requests
"""
import json, os, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
PRICE_PER_MTOK = 0.042  # jev-1.13 input price
ALL_CASES = [(0, 0), (0, 1), (1, 0), (1, 1)]


def load_env():  # read KEY=VALUE lines from .env next to this script (no dependency)
    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        for line in open(path):
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k.removeprefix("export ").strip(), v.strip().strip("'\""))


def load(path):
    with open(path) as f:
        return json.load(f)


# ───────────────────────────── Jev ─────────────────────────────

class Stats:
    def __init__(self):
        self.requests = self.input_tokens = self.output_tokens = 0
        self.cycles = self.gate_evals = 0
        self.api_seconds = 0.0
        self.started = time.time()
        self.answers = {}  # (a, b) -> last p(yes) Jev gave for that gate case

    @property
    def seconds(self):
        return time.time() - self.started


def ask_gates(m, cases, st):
    """One Jev request: one Noul per gate case. Returns {(a, b): p(yes)}."""
    ids = {f"g{a}{b}": (a, b) for a, b in cases}
    body = json.dumps({
        "model": m["model"],
        "state": {k: {"a": a, "b": b} for k, (a, b) in ids.items()},
        "questions": {k: {"type": "noul", "instructions": m["gate"]["instructions"].format(id=k)}
                      for k in ids},
    }).encode()
    for t in range(6):
        t0 = time.time()
        try:
            req = urllib.request.Request(m["api"], body, {"Content-Type": "application/json",
                  "Authorization": "Bearer " + os.environ["TYPESAFE_API_KEY"]})
            r = json.load(urllib.request.urlopen(req, timeout=60))
            break
        except urllib.error.URLError as e:  # retry 429 / 5xx / network, fail on other 4xx
            if getattr(e, "code", 500) < 500 and e.code != 429:
                raise RuntimeError(f"API {e.code}: {e.read()[:200]}")
            time.sleep(2 ** t)
    else: raise RuntimeError("API unreachable after retries")
    st.requests += 1
    st.api_seconds += time.time() - t0
    st.input_tokens += r.get("usage", {}).get("input_tokens", 0)
    st.output_tokens += r.get("usage", {}).get("output_tokens", 0)
    ps = {ids[k]: a["noul"] for k, a in r["answers"].items()}
    st.answers.update(ps)
    return ps


# ───────────────────────────── the circuit ─────────────────────────────

def run(m, init, max_cycles=4000, on_cycle=None):
    """Run the circuit from `init` register values until its halt bit.
    One request asks Jev all 4 gate cases; every gate in the machine uses those answers.
    Returns (displayed values, Stats)."""
    st = Stats()
    yes = m["gate"]["bit_if_yes"]
    table = {ab: yes if p >= 0.5 else 1 - yes for ab, p in ask_gates(m, ALL_CASES, st).items()}

    bits = [(init.get(r, 0) >> i) & 1 for r, w in m["registers"] for i in range(w)]
    num = lambda v, nodes: sum(v[n] << i for i, n in enumerate(nodes))
    offs, o = {}, 0
    for r, w in m["registers"]: offs[r] = list(range(o, o + w)); o += w
    out = []
    for cycle in range(max_cycles):
        v, g = bits + [0] * len(m["gates"]), len(bits)
        for size in m["level_sizes"]:  # gates in one level don't depend on each other
            level = m["gates"][g - len(bits):g - len(bits) + size]
            for i, (x, y) in enumerate(level): v[g + i] = table[(v[x], v[y])]
            g += size
        st.cycles += 1
        st.gate_evals += len(m["gates"])
        shown = num(v, m["display"]["show"]) if v[m["display"]["when"]] else None
        if on_cycle: on_cycle(cycle, {r: num(v, offs[r]) for r, _ in m["registers"]}, shown, st)
        if v[m["halt"]]: return out, st
        if shown is not None: out.append(shown)
        bits = [v[n] for n in m["next"]]  # clock edge
    raise RuntimeError(f"no HLT after {max_cycles} clock cycles — does the program loop forever? (e.g. dividing by 0)")


# ───────────────────────────── programs (JSON) ─────────────────────────────

def assemble(m, prog, inputs=None):
    """Program JSON -> initial register values, using the instruction set in cpu.json."""
    isa, words = m["isa"], m["isa"]["words"]
    code, data, names = prog.get("code", []), prog.get("data", {}), prog.get("inputs", {})
    if len(code) > words:
        raise ValueError(f"program has {len(code)} instructions; RAM holds {words} bytes")
    ram = [0] * words
    for i, line in enumerate(code):
        op, *arg = line.split()
        if op.upper() not in isa["opcodes"]:
            raise ValueError(f"line {i}: unknown instruction {op!r}; known: {', '.join(isa['opcodes'])}")
        ram[i] = (isa["opcodes"][op.upper()] << isa["operand_bits"]) | (int(arg[0]) if arg else 0)
    for addr, value in data.items():
        if int(addr) < len(code):
            raise ValueError(f"data at address {addr} overlaps the code (addresses 0–{len(code) - 1})")
        ram[int(addr)] = value
    for name, value in (inputs or {}).items():
        if name not in names:
            raise ValueError(f"unknown input {name!r}; this program takes: {', '.join(names) or 'none'}")
        if not 0 <= value <= 255:
            raise ValueError(f"{name}={value}: inputs are 8-bit (0–255)")
        ram[names[name]] = value
    return {f"{isa['memory']}{k}": v for k, v in enumerate(ram)}


def read_result(prog, out):
    """Turn the output port values into the answer, as the program's "result" rule says."""
    rule = prog.get("result")
    if rule is None:
        return out                          # no rule: the answer is everything printed
    if not out:
        return rule.get("no_output", "no output")
    return out[0] + rule.get("second_output_adds", 0) * (len(out) > 1)


def disasm(m, byte):
    names = {v: k for k, v in m["isa"]["opcodes"].items()}
    bits = m["isa"]["operand_bits"]
    name = names.get(byte >> bits, "?")
    return name if name in ("NOP", "OUT", "HLT") else f"{name} {byte & ((1 << bits) - 1)}"


def execute(m, prog, inputs, trace=False):
    def show(cycle, regs, shown, st):
        ins = disasm(m, regs[f"{m['isa']['memory']}{regs['pc']}"])
        print(f"  {cycle:5d}  pc={regs['pc']:2d}  {ins:<7} A={regs['a']:3d} C={regs['c']}"
              + (f"   ▶ OUT {shown}" if shown is not None and ins != "HLT" else ""))
    out, st = run(m, assemble(m, prog, inputs), on_cycle=show if trace else None)
    return read_result(prog, out), out, st


def usage(st):
    return (f"jev: {st.requests} request{'s' * (st.requests != 1)} · {st.input_tokens:,} input tokens "
            f"(${st.input_tokens / 1e6 * PRICE_PER_MTOK:.6f}) · {st.seconds:.1f} s · "
            f"{st.cycles:,} clock cycles · {st.gate_evals:,} gate evaluations")


def test(m, prog):
    tests, bad, requests, tokens, t0 = prog.get("tests", []), 0, 0, 0, time.time()
    if not tests:
        sys.exit("this program has no \"tests\" in its JSON")
    for case in tests:
        inputs = case.get("with", {})
        answer, _, st = execute(m, prog, inputs)
        ok = answer == case["expect"]
        bad += not ok
        requests += st.requests
        tokens += st.input_tokens
        label = " ".join(f"{k}={v}" for k, v in inputs.items()) or "(defaults)"
        print(f"{'PASS' if ok else 'FAIL'}  {label:<16} → {answer}"
              + ("" if ok else f"   expected {case['expect']}") + f"   ({st.cycles} cycles)")
    print(f"\n{len(tests) - bad}/{len(tests)} passed · {requests} Jev requests · "
          f"{tokens:,} input tokens · {time.time() - t0:.1f} s")
    return bad == 0


def selftest(m, n):
    """Ask the exact production request n times; every case must land on the right side."""
    st, ok = Stats(), True
    yes = m["gate"]["bit_if_yes"]
    runs = [ask_gates(m, ALL_CASES, st) for _ in range(n)]
    for ab in ALL_CASES:
        want = yes ^ (1 - (ab[0] & ab[1]))
        ps = [r[ab] for r in runs]
        good = all((yes if p >= 0.5 else 1 - yes) == want for p in ps)
        ok &= good
        print(f"  a={ab[0]} b={ab[1]}  NAND={want}  p(yes): "
              f"{' '.join(f'{p:.2f}' for p in ps)}  {'PASS' if good else 'FAIL'}")
    print(f"{'ALL PASS' if ok else 'FAILED'} — {st.requests} requests, "
          f"{st.input_tokens} input tokens ({st.input_tokens // max(n, 1)} per request)")
    return ok


def main():
    load_env()
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    m = load(os.path.join(HERE, "cpu.json"))
    unknown = flags - {"--trace", "--test", "--selftest"}
    if unknown:
        sys.exit(f"unknown option {', '.join(sorted(unknown))} — options are --trace, --test, --selftest")
    if not os.environ.get("TYPESAFE_API_KEY"):
        sys.exit("TYPESAFE_API_KEY not found — put it in .env")
    if "--selftest" in flags:
        sys.exit(0 if selftest(m, 5) else 1)
    if not args:
        sys.exit(__doc__)

    prog = load(args[0])
    inputs = {}
    for a in args[1:]:
        k, sep, v = a.partition("=")
        if not sep or not v.isdigit():
            sys.exit(f"inputs look like name=number, got {a!r}")
        inputs[k] = int(v)
    if prog.get("about"):
        print(prog["about"])
    try:
        if "--test" in flags:
            sys.exit(0 if test(m, prog) else 1)
        answer, out, st = execute(m, prog, inputs, trace="--trace" in flags)
    except (ValueError, RuntimeError) as e:
        sys.exit(f"error: {e}")
    if prog.get("result") is not None:
        print(f"output port: {out}")
    print(f"answer: {answer}")
    print(usage(st))


if __name__ == "__main__":
    main()
