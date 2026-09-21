#!/usr/bin/env python3
"""
jev_run.py — run a program on the Jev computer.

The machine is cpu.json (a NAND netlist); every gate is answered by Jev.
Programs are JSON files in programs/ (see README for the format).

    python jev_run.py programs/fib.json                 # run it (1 Jev request)
    python jev_run.py programs/add.json a=40 b=3        # set the program's inputs
    python jev_run.py programs/bubble_sort.json list=5,2,9,1,7,3   # a list input
    python jev_run.py programs/add.json a=40 b=3 --trace   # show every instruction
    python jev_run.py programs/add.json --test          # run the tests inside the JSON
    python jev_run.py --selftest                        # check the gate answers, 5 requests
    python jev_run.py programs/add.json --cpu=mini      # pick a CPU model from cpus/

Programs inside cpus/<model>/programs/ run on that model automatically.
"""
import json, os, re, sys, time, urllib.request, urllib.error

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

def run(m, init, max_cycles=100_000, on_cycle=None):
    """Run the circuit from `init` register values until its halt bit.
    One request asks Jev all 4 gate cases; every gate in the machine uses those answers.
    Returns (displayed values, Stats)."""
    st = Stats()
    yes = m["gate"]["bit_if_yes"]
    answers = ask_gates(m, ALL_CASES, st)
    table = [yes if answers[ab] >= 0.5 else 1 - yes for ab in ALL_CASES]  # index = 2a + b

    bits = [(init.get(r, 0) >> i) & 1 for r, w in m["registers"] for i in range(w)]
    num = lambda v, nodes: sum(v[n] << i for i, n in enumerate(nodes))
    offs, o = {}, 0
    for r, w in m["registers"]: offs[r] = list(range(o, o + w)); o += w
    gates, show, out = m["gates"], m["display"]["show"], []
    for cycle in range(max_cycles):
        v = list(bits)
        for x, y in gates:  # gates are stored in dependency order
            v.append(table[2 * v[x] + v[y]])
        st.cycles += 1
        st.gate_evals += len(gates)
        shown = num(v, show) if v[m["display"]["when"]] else None
        if on_cycle: on_cycle(cycle, {r: num(v, offs[r]) for r in offs}, shown, st)
        if v[m["halt"]]: return out, st
        if shown is not None: out.append(shown)
        bits = [v[n] for n in m["next"]]  # clock edge
    raise RuntimeError(f"no HLT after {max_cycles:,} clock cycles — does the program loop forever? (e.g. dividing by 0)")


# ───────────────────────────── programs (JSON) ─────────────────────────────

def assemble(m, prog, inputs=None):
    """Program JSON -> initial RAM, using the instruction set in cpu.json.
    Code comes first (2 bytes per instruction), then each data item in order.
    Operands can be numbers, labels, data names, or name+n / name-n."""
    isa, words = m["isa"], m["isa"]["words"]
    size, labels, lines = isa["instruction_bytes"], {}, []
    for i, line in enumerate(prog.get("code", [])):
        label, sep, rest = line.rpartition(":")
        if sep:
            labels[label.strip()] = i * size
        lines.append(rest.split())
    addr, data = len(lines) * size, prog.get("data", {})
    for name, value in data.items():
        labels[name] = addr
        addr += len(value) if isinstance(value, list) else 1
    if addr > words:
        raise ValueError(f"program needs {addr} bytes; RAM holds {words}")

    def operand(text, i):
        if text.isdigit():
            return int(text)
        name, sign, offset = re.fullmatch(r"(\w+)(?:([+-])(\d+))?", text).groups()
        if name not in labels:
            raise ValueError(f"line {i}: unknown label or data name {name!r}")
        return (labels[name] + (int(offset) if sign == "+" else -int(offset or 0))) % words

    ram = [0] * words
    for i, parts in enumerate(lines):
        if not parts or parts[0].upper() not in isa["opcodes"]:
            raise ValueError(f"line {i}: unknown instruction {' '.join(parts)!r}; "
                             f"known: {', '.join(isa['opcodes'])}")
        ram[i * size] = isa["opcodes"][parts[0].upper()]
        ram[i * size + 1] = operand(parts[1], i) if len(parts) > 1 else 0

    values = dict(data)
    for name, value in (inputs or {}).items():
        if name not in prog.get("inputs", []):
            raise ValueError(f"unknown input {name!r}; this program takes: "
                             f"{', '.join(prog.get('inputs', [])) or 'none'}")
        if isinstance(data[name], list) and (not isinstance(value, list) or len(value) != len(data[name])):
            raise ValueError(f"{name} is a list of {len(data[name])} numbers, like {name}={','.join(map(str, data[name]))}")
        values[name] = value
    for name, value in values.items():
        for k, byte in enumerate(value if isinstance(value, list) else [value]):
            if not 0 <= byte <= 255:
                raise ValueError(f"{name}={value}: values are 8-bit (0–255)")
            ram[labels[name] + k] = byte
    return {f"{isa['memory']}{k}": v for k, v in enumerate(ram)}


def read_result(prog, out):
    """Turn the output port values into the answer, as the program's "result" rule says."""
    rule = prog.get("result")
    if rule is None:
        return out                          # no rule: the answer is everything printed
    if not out:
        return rule.get("no_output", "no output")
    return out[0] + rule.get("add", 0) + rule.get("second_output_adds", 0) * (len(out) > 1)


def disasm(m, op, arg):
    name = {v: k for k, v in m["isa"]["opcodes"].items()}.get(op & 15, "?")
    return name if name in ("NOP", "OUT", "HLT") else f"{name} {arg}"


def execute(m, prog, inputs, trace=False):
    mem, words = m["isa"]["memory"], m["isa"]["words"]
    def show(cycle, regs, shown, st):
        pc = regs["pc"]
        ins = disasm(m, regs[f"{mem}{pc}"], regs[f"{mem}{(pc + 1) % words}"])
        print(f"  {cycle:6d}  pc={pc:3d}  {ins:<8} A={regs['a']:3d} C={regs['c']}"
              + (f"   ▶ OUT {shown}" if shown is not None and ins != "HLT" else ""))
    out, st = run(m, assemble(m, prog, inputs), on_cycle=show if trace else None)
    return read_result(prog, out), out, st


def usage(st, m):
    return (f"cpu: {m.get('cpu', 'classic')} · jev: {st.requests} request{'s' * (st.requests != 1)} · {st.input_tokens:,} input tokens "
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
        label = " ".join(f"{k}={','.join(map(str, v)) if isinstance(v, list) else v}"
                         for k, v in inputs.items()) or "(defaults)"
        print(f"{'PASS' if ok else 'FAIL'}  {label:<22} → {answer}"
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


def machine_path(flags, program):
    """--cpu=NAME picks cpus/NAME/cpu.json; a program in cpus/NAME/programs/ uses that model;
    otherwise the classic cpu.json next to this script."""
    for f in flags:
        if f.startswith("--cpu="):
            name = f.split("=", 1)[1]
            path = os.path.join(HERE, "cpu.json") if name == "classic" else os.path.join(HERE, "cpus", name, "cpu.json")
            if not os.path.exists(path):
                models = ["classic"] + sorted(os.listdir(os.path.join(HERE, "cpus")))
                sys.exit(f"unknown cpu {name!r}; models: {', '.join(models)}")
            return path
    if program:
        beside = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(program))), "cpu.json")
        if os.path.exists(beside):
            return beside
    return os.path.join(HERE, "cpu.json")


def main():
    load_env()
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    unknown = {f for f in flags if f not in ("--trace", "--test", "--selftest") and not f.startswith("--cpu=")}
    if unknown:
        sys.exit(f"unknown option {', '.join(sorted(unknown))} — options are --trace, --test, --selftest, --cpu=NAME")
    m = load(machine_path(flags, args[0] if args else None))
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
        if not sep or not all(x.isdigit() for x in v.split(",")):
            sys.exit(f"inputs look like name=5 or list=4,7,9 — got {a!r}")
        inputs[k] = [int(x) for x in v.split(",")] if "," in v else int(v)
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
    print(usage(st, m))


if __name__ == "__main__":
    main()
