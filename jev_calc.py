#!/usr/bin/env python3
"""
jev_calc.py — a calculator whose arithmetic runs on the Jev CPU (cpu.json).

Python here is only the keypad and the screen. Each operator is a tiny program
loaded into the Jev computer's 16-byte RAM; the CPU (2,102 NAND gates, whose
truth table Jev answers in one request per calculation) runs it and OUTputs
the answer. The "How it worked" panel shows every step.

    python jev_calc.py            # open the calculator
    python jev_calc.py --sim      # ideal gates, no API (for trying the UI)
    python jev_calc.py --test     # headless check of all operators on ideal gates

Numbers are 8-bit: operands 0–255.
    +  sum up to 510 (the carry is output as the 9th bit)
    −  negative results come out as two's complement plus a borrow flag
    ×  products over 255 are reported as overflow
    ÷  integer quotient; ÷0 is rejected before running
"""
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk

from jev_computer import OPNAME, asm
from jev_run import load, run

HERE = os.path.dirname(os.path.abspath(__file__))
MACHINE = load(os.path.join(HERE, "cpu.json"))
PRICE_PER_MTOK = 0.042          # jev-1.13 input price (docs.typesafe.ai/models)
MAX_CYCLES = 4000
TRACE_HEAD, TRACE_TAIL = 60, 20  # long runs show the start and the end of the trace

# Data lives at the top of RAM: 12 = const 1, 13..15 = operands/work.
PROGRAMS = {
    "+": asm(["LDA 14", "ADD 15", "OUT", "JC 5", "HLT",
              "LDI 1", "OUT", "HLT"]),                     # OUT sum, then carry if any
    "−": asm(["LDA 14", "SUB 15", "OUT", "JC 6",
              "LDI 1", "OUT", "HLT"]),                     # OUT diff, then 1 if borrow
    "×": asm(["LDA 15", "JZ 9", "SUB 12", "STA 15",        # while counter: counter -= 1
              "LDA 13", "ADD 14", "JC 11", "STA 13",       #   acc += a (halt silently on overflow)
              "JMP 0", "LDA 13", "OUT", "HLT"], {12: 1}),
    "÷": asm(["LDA 13", "SUB 14", "JC 6", "LDA 15", "OUT", "HLT",   # if a < b: OUT q
              "STA 13", "LDA 15", "ADD 12", "STA 15", "JMP 0"],     # else a -= b, q += 1
             {12: 1}),
}
DATA_LABELS = {
    "+": {14: "a", 15: "b"},
    "−": {14: "a", 15: "b"},
    "×": {12: "constant 1", 13: "accumulator", 14: "a", 15: "b (counter)"},
    "÷": {12: "constant 1", 13: "a (what is left)", 14: "b", 15: "quotient"},
}
PROGRAM_TEXT = {
    "+": "A = a + b, OUT A; if the adder carried, also OUT 1 (bit 8).",
    "−": "A = a − b, OUT A; if it borrowed (a < b), also OUT 1 = negative.",
    "×": "Repeated addition: add a, b times (stops early on overflow).",
    "÷": "Repeated subtraction: subtract b while a ≥ b, counting the rounds.",
}


def load_program(op, a, b):
    """RAM image for `a op b` — only places operands, the CPU does the math."""
    ram = list(PROGRAMS[op])
    if op in "+−":
        ram[14], ram[15] = a, b
    elif op == "×":
        ram[13], ram[14], ram[15] = 0, a, b  # add a, b times
    else:
        ram[13], ram[14], ram[15] = a, b, 0
    return ram


def interpret(op, out):
    """Turn the CPU's OUT values into (display string, explanation)."""
    if op == "+":
        if len(out) > 1:
            return str(out[0] + 256), f"low 8 bits {out[0]} + carry bit 8 (256)"
        return str(out[0]), "no carry"
    if op == "−":
        if len(out) > 1:
            return str(out[0] - 256), f"{out[0]} with borrow flag → {out[0]} − 256"
        return str(out[0]), "no borrow"
    if op == "×" and not out:
        return "overflow (> 255)", "adder carried out of 8 bits → program halted without OUT"
    return str(out[0]), "OUT value"


def disasm(byte):
    name = OPNAME.get(byte >> 4, "???")
    return name if name in ("NOP", "OUT", "HLT") else f"{name} {byte & 15}"


def calculate(op, a, b, sim=False, on_cycle=None):
    if op == "÷" and b == 0:
        raise ZeroDivisionError("÷ 0")
    ram = load_program(op, a, b)
    out, st = run(MACHINE, {f"ram{k}": v for k, v in enumerate(ram)}, sim=sim,
                  max_cycles=MAX_CYCLES, on_cycle=on_cycle)
    return interpret(op, out), st


def reference(op, a, b):
    return {"+": str(a + b), "−": str(a - b), "÷": str(a // b) if b else None,
            "×": str(a * b) if a * b <= 255 else "overflow (> 255)"}[op]


def self_test():
    cases = [(op, a, b) for op in "+−×÷" for a, b in
             [(0, 0), (7, 6), (255, 1), (1, 255), (200, 100), (100, 200), (15, 17), (255, 255), (12, 0)]]
    bad = 0
    for op, a, b in cases:
        if op == "÷" and b == 0:
            continue
        (got, _), st = calculate(op, a, b, sim=True)
        ok = got == reference(op, a, b)
        bad += not ok
        print(f"{'ok ' if ok else 'BAD'} {a:3d} {op} {b:3d} = {got:<18} ({st.cycles} cycles)")
    print("ALL OK" if not bad else f"{bad} FAILED")
    return not bad


def explain(op, a, b, ram, trace, st, out, result, why, sim):
    """Text for the "How it worked" panel."""
    L = []
    L.append(f"① Load program for {a} {op} {b} into RAM (16 bytes)")
    L.append(f"   {PROGRAM_TEXT[op]}")
    L.append("   addr  byte  content")
    labels = DATA_LABELS[op]
    last_code = max(i for i, v in enumerate(ram) if v and i not in labels)
    for i, v in enumerate(ram):
        if i in labels:
            L.append(f"   {i:4d}  {v:4d}  data: {labels[i]} = {v}")
        elif i <= last_code:
            L.append(f"   {i:4d}  {v:4d}  {disasm(v)}")
    L.append("")

    L.append("② Ask Jev the NAND truth table" + (" (sim: ideal gates, no API)" if sim else
             f" — {st.requests} request, {st.input_tokens} input tokens"))
    L.append('   question per case: "Are both a and b equal to 1?"  → NAND = NOT(yes)')
    L.append("   a b   p(yes)   NAND")
    for (x, y) in [(0, 0), (0, 1), (1, 0), (1, 1)]:
        p = st.answers.get((x, y))
        ps = f"{p:5.2f}" if p is not None else "  —  "
        nand = 1 - (x & y) if p is None else int(p < 0.5)  # from Jev's answer, not from Python
        L.append(f"   {x} {y}   {ps}     {nand}")
    L.append(f"   every gate in the CPU now takes its output from this table")
    L.append("")

    L.append(f"③ CPU runs {st.cycles} clock cycles × {len(MACHINE['gates']):,} NAND gates "
             f"= {st.gate_evals:,} gate evaluations")
    L.append("   cycle  pc  instruction   A    C")
    rows = []
    for cycle, regs, shown in trace:
        ins = disasm(regs[f"ram{regs['pc']}"])
        row = f"   {cycle:5d}  {regs['pc']:2d}  {ins:<12} {regs['a']:3d}   {regs['c']}"
        if shown is not None:
            row += f"   ▶ OUT {shown}"
        rows.append(row)
    if len(rows) > TRACE_HEAD + TRACE_TAIL:
        hidden = len(rows) - TRACE_HEAD - TRACE_TAIL
        rows = rows[:TRACE_HEAD] + [f"   … {hidden:,} more cycles of the same loop …"] + rows[-TRACE_TAIL:]
    L += rows
    L.append("")

    L.append(f"④ Read the output port: OUT = {out}")
    L.append(f"   {why}")
    L.append(f"   {a} {op} {b} = {result}")
    return "\n".join(L)


# ──────────────────────────────── UI ────────────────────────────────

class Calculator:
    def __init__(self, root, sim):
        self.root, self.sim = root, sim
        self.a = self.op = None
        self.entry = ""
        self.events = queue.Queue()
        self.busy = False

        root.title("Jev Calculator")
        root.configure(padx=14, pady=14)
        left = ttk.Frame(root)
        left.grid(row=0, column=0, sticky="n")
        right = ttk.LabelFrame(root, text="How it worked", padding=6)
        right.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        self.expr = tk.StringVar(value="")
        self.display = tk.StringVar(value="0")
        ttk.Label(left, textvariable=self.expr, anchor="e", font=("Menlo", 13),
                  foreground="#888").grid(row=0, column=0, columnspan=4, sticky="ew")
        ttk.Label(left, textvariable=self.display, anchor="e",
                  font=("Menlo", 30, "bold")).grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, 10))

        keys = ["7", "8", "9", "÷", "4", "5", "6", "×", "1", "2", "3", "−", "C", "0", "=", "+"]
        for i, k in enumerate(keys):
            tk.Button(left, text=k, width=5, height=2, font=("Menlo", 16),
                      command=lambda k=k: self.press(k)).grid(row=2 + i // 4, column=i % 4, padx=2, pady=2)
        for key, sym in {"+": "+", "-": "−", "*": "×", "/": "÷", "=": "=", "c": "C"}.items():
            root.bind(key, lambda e, s=sym: self.press(s))
        root.bind("<Return>", lambda e: self.press("="))
        root.bind("<Escape>", lambda e: self.press("C"))
        for d in "0123456789":
            root.bind(d, lambda e, d=d: self.press(d))

        stats = ttk.LabelFrame(left, text="Jev usage (last calculation)", padding=8)
        stats.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        self.stat_vars = {}
        for i, name in enumerate(["Jev requests", "Input tokens", "Output tokens", "Cost (USD)",
                                  "Total time", "Time waiting on Jev", "CPU clock cycles",
                                  "Gate evaluations"]):
            ttk.Label(stats, text=name).grid(row=i, column=0, sticky="w")
            v = tk.StringVar(value="–")
            ttk.Label(stats, textvariable=v, font=("Menlo", 12)).grid(row=i, column=1, sticky="e", padx=(20, 0))
            self.stat_vars[name] = v
        stats.columnconfigure(1, weight=1)

        self.status = tk.StringVar(value="sim mode: ideal gates, no API" if sim else "ready")
        ttk.Label(left, textvariable=self.status, foreground="#888",
                  wraplength=300).grid(row=7, column=0, columnspan=4, sticky="w", pady=(8, 0))

        self.steps = tk.Text(right, width=62, height=34, font=("Menlo", 11), wrap="none",
                             relief="flat", padx=6, pady=6)
        scroll = ttk.Scrollbar(right, command=self.steps.yview)
        self.steps.configure(yscrollcommand=scroll.set)
        self.steps.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.set_steps("Type a calculation and press =.\n\n"
                       "① the operator's program is loaded into the Jev computer's RAM\n"
                       "② Jev answers the 4 NAND gate cases in one request\n"
                       "③ the CPU (2,102 NAND gates) runs the program clock by clock\n"
                       "④ the answer is read from the CPU's output port")

        root.after(50, self.poll)

    def set_steps(self, text):
        self.steps.configure(state="normal")
        self.steps.delete("1.0", "end")
        self.steps.insert("1.0", text)
        self.steps.configure(state="disabled")

    # ── keypad logic (no arithmetic here: only building the question) ──
    def press(self, k):
        if self.busy:
            return
        if k.isdigit():
            if len(self.entry) < 3 and int(self.entry + k) <= 255:
                self.entry = (self.entry + k).lstrip("0") or "0"
                self.display.set(self.entry)
            else:
                self.status.set("operands are 8-bit: 0–255")
        elif k == "C":
            self.a = self.op = None
            self.entry = ""
            self.expr.set("")
            self.display.set("0")
            self.status.set("ready")
        elif k in "+−×÷":
            if self.entry:
                self.a = int(self.entry)
            elif self.a is None:
                self.a = 0
            self.op, self.entry = k, ""
            self.expr.set(f"{self.a} {k}")
        elif k == "=" and self.op is not None:
            b = int(self.entry or "0")
            self.expr.set(f"{self.a} {self.op} {b} =")
            self.start(self.op, self.a, b)

    def start(self, op, a, b):
        if op == "÷" and b == 0:
            self.display.set("Error")
            self.status.set("division by zero would loop forever on the CPU")
            return
        self.busy = True
        for s in self.stat_vars.values():
            s.set("…")
        self.display.set("…")
        self.status.set("asking Jev and running the CPU…")

        def work():
            trace = []
            try:
                (result, why), st = calculate(
                    op, a, b, sim=self.sim,
                    on_cycle=lambda c, regs, shown, _st: trace.append((c, regs, shown)))
                out = [s for _, _, s in trace[:-1] if s is not None]
                text = explain(op, a, b, load_program(op, a, b), trace, st, out, result, why, self.sim)
                self.events.put(("done", op, a, b, result, st, text))
            except Exception as e:
                self.events.put(("error", str(e)))

        threading.Thread(target=work, daemon=True).start()

    def show_stats(self, st):
        v = self.stat_vars
        v["Jev requests"].set(f"{st.requests:,}")
        v["Input tokens"].set(f"{st.input_tokens:,}")
        v["Output tokens"].set(f"{st.output_tokens:,}")
        v["Cost (USD)"].set(f"${st.input_tokens / 1e6 * PRICE_PER_MTOK:.8f}")
        v["Total time"].set(f"{st.seconds:.2f} s")
        v["Time waiting on Jev"].set(f"{st.api_seconds:.2f} s")
        v["CPU clock cycles"].set(f"{st.cycles:,}")
        v["Gate evaluations"].set(f"{st.gate_evals:,}")

    def poll(self):
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "done":
                    _, op, a, b, result, st, text = ev
                    self.display.set(result)
                    self.show_stats(st)
                    self.set_steps(text)
                    self.status.set(f"{a} {op} {b} = {result}  — computed by the Jev CPU")
                    self.a = int(result) if result.isdigit() and int(result) <= 255 else None
                    self.op, self.entry = None, ""
                else:
                    self.display.set("Error")
                    self.status.set(ev[1])
                self.busy = False
        except queue.Empty:
            pass
        self.root.after(50, self.poll)


def main():
    if "--test" in sys.argv:
        sys.exit(0 if self_test() else 1)
    sim = "--sim" in sys.argv
    if not sim and not os.environ.get("TYPESAFE_API_KEY"):
        sys.exit("TYPESAFE_API_KEY not found (put it in .env), or run with --sim")
    root = tk.Tk()
    Calculator(root, sim)
    root.mainloop()


if __name__ == "__main__":
    main()
