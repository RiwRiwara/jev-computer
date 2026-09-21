#!/usr/bin/env python3
"""Generic Jev circuit runner: holds bits, asks Jev about gates, ticks the clock.
Knows nothing about CPUs — the machine is entirely in the circuit JSON.
usage: python jev_run.py cpu.json programs/fib.json [--live] [--sim]
       python jev_run.py cpu.json --selftest [N]
   or: from jev_run import load, run"""
import json, os, sys, time, urllib.request, urllib.error

def load_env():  # read KEY=VALUE lines from .env next to this script (no dependency)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        for line in open(path):
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k.removeprefix("export ").strip(), v.strip().strip("'\""))
load_env()

ALL_CASES = [(0, 0), (0, 1), (1, 0), (1, 1)]


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


class Stopped(Exception):
    pass


def load(path):
    with open(path) as f:
        return json.load(f)


def ask_gates(m, cases, st, stop=None):
    """One Jev request: one Noul per gate case. Returns {(a, b): p(yes)}."""
    ids = {f"g{a}{b}": (a, b) for a, b in cases}
    body = json.dumps({
        "model": m["model"],
        "state": {k: {"a": a, "b": b} for k, (a, b) in ids.items()},
        "questions": {k: {"type": "noul", "instructions": m["gate"]["instructions"].format(id=k)}
                      for k in ids},
    }).encode()
    for t in range(6):
        if stop and stop.is_set(): raise Stopped
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


def run(m, init, live=False, sim=False, max_cycles=1000, on_cycle=None, stop=None):
    """Run the circuit from `init` register values until its halt bit.
    fast (default): one request asks Jev all 4 gate cases; every gate uses those answers.
    live: one request per circuit level, every clock, for the cases at that level.
    Returns (displayed values, Stats)."""
    st = Stats()
    yes = m["gate"]["bit_if_yes"]
    to_bit = lambda p: yes if p >= 0.5 else 1 - yes
    if sim:
        table = {ab: yes ^ (1 - (ab[0] & ab[1])) for ab in ALL_CASES}
    elif not live:
        table = {ab: to_bit(p) for ab, p in ask_gates(m, ALL_CASES, st, stop).items()}

    bits = [(init.get(r, 0) >> i) & 1 for r, w in m["registers"] for i in range(w)]
    num = lambda v, nodes: sum(v[n] << i for i, n in enumerate(nodes))
    offs, o = {}, 0
    for r, w in m["registers"]: offs[r] = list(range(o, o + w)); o += w
    out = []
    for cycle in range(max_cycles):
        v, g = bits + [0] * len(m["gates"]), len(bits)
        for size in m["level_sizes"]:  # gates in one level don't depend on each other
            level = m["gates"][g - len(bits):g - len(bits) + size]
            if live and not sim:
                pats = sorted({(v[x], v[y]) for x, y in level})
                table = {ab: to_bit(p) for ab, p in ask_gates(m, pats, st, stop).items()}
            for i, (x, y) in enumerate(level): v[g + i] = table[(v[x], v[y])]
            g += size
        st.cycles += 1
        st.gate_evals += len(m["gates"])
        shown = num(v, m["display"]["show"]) if v[m["display"]["when"]] else None
        if on_cycle: on_cycle(cycle, {r: num(v, offs[r]) for r, _ in m["registers"]}, shown, st)
        if v[m["halt"]]: return out, st
        if shown is not None: out.append(shown)
        bits = [v[n] for n in m["next"]]  # clock edge
    raise RuntimeError(f"no halt after {max_cycles} cycles")


def selftest(m, n):
    """Ask the exact production request n times; every case must land on the right side."""
    st, ok = Stats(), True
    want = {ab: m["gate"]["bit_if_yes"] ^ (1 - (ab[0] & ab[1])) for ab in ALL_CASES}
    runs = [ask_gates(m, ALL_CASES, st) for _ in range(n)]
    for ab in ALL_CASES:
        ps = [r[ab] for r in runs]
        good = all((m["gate"]["bit_if_yes"] if p >= 0.5 else 1 - m["gate"]["bit_if_yes"]) == want[ab]
                   for p in ps)
        ok &= good
        print(f"  a={ab[0]} b={ab[1]}  NAND={want[ab]}  p(yes): "
              f"{' '.join(f'{p:.2f}' for p in ps)}  {'PASS' if good else 'FAIL'}")
    print(f"{'ALL PASS' if ok else 'FAILED'} — {st.requests} requests, "
          f"{st.input_tokens} input tokens ({st.input_tokens // max(n, 1)} per request)")
    return ok


if __name__ == "__main__":
    m = load(sys.argv[1])
    if sys.argv[2] == "--selftest":
        sys.exit(0 if selftest(m, int(sys.argv[3]) if len(sys.argv) > 3 else 5) else 1)
    init = load(sys.argv[2])
    def show(cycle, regs, shown, st):
        print(f"{cycle:4d} " + " ".join(f"{r}={regs[r]:3d}" for r in m["trace"]) +
              f"  jev_calls={st.requests}  {st.seconds:6.1f}s", flush=True)
        if shown is not None: print("  ▶", shown, flush=True)
    out, st = run(m, init, live="--live" in sys.argv, sim="--sim" in sys.argv, on_cycle=show)
    print("output:", out)
    print(f"{st.requests} Jev requests, {st.input_tokens} input / {st.output_tokens} output tokens")
