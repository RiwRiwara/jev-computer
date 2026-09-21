# jev-computer

**An 8-bit computer built from one yes/no question asked to an AI.**

[Jev](https://docs.typesafe.ai) (TypeSafe's System One model) doesn't do arithmetic, doesn't know what a CPU is, and writes no code. It is asked a single thing through its API:

> *"Are both `a` and `b` equal to 1?"*

Its answers to the four possible inputs become an AND gate, which is inverted to NAND. NAND is a universal gate, so 2,102 of them wired together make a working computer. It runs Fibonacci, counts down, multiplies, and divides.

**▶ [Play with it in the browser](https://riwriwara.github.io/jev-computer/)**. The demo uses a real recorded Jev API response, so no key is needed.

```
one Jev API call ──► NAND truth table ──► full adder (9 NANDs) ──► ALU ──► 8-bit CPU + RAM
   446 tokens          p = .00 .01 .01 .99       2,102 NAND gates, 59 levels deep
```

## Results (real runs against `jev-1.13.0`)

| Run | Result | Requests | Input tokens | Time |
|---|---|---:|---:|---:|
| Gate selftest, 5 repeats × 4 cases | 20/20 correct, p = 0.00–0.01 / 0.99 | 5 | 2,230 | — |
| Fibonacci (fast mode) | `0 1 1 2 3 5 8 13 21 34 55 89 144` | 1 | 446 | < 1 s |
| 40 + 3 (fast mode) | `43` | 1 | 446 | 0.8 s |
| 40 + 3 (**live**: Jev asked at every circuit level, every clock) | `43` | 295 | 112,441 | 245 s |
| Countdown (**live**, older 1-case-per-request prompt) | `5 4 3 2 1 0` | 3,642 | not measured | 1,175 s |

The fast-mode Jev call costs about **$0.00002** (input $0.042 per million tokens).

## Quick start

Everything uses the Python standard library (3.9+). There's nothing to install.

```bash
cp .env.example .env            # put your TypeSafe API key in .env

python jev_run.py cpu.json --selftest            # verify the gate (5 requests)
python jev_run.py cpu.json programs/fib.json     # run Fibonacci on the Jev CPU
python jev_run.py cpu.json programs/fib.json --live   # Jev answers every gate level (slow!)
python jev_calc.py                               # desktop calculator (+ − × ÷) with a step-by-step panel
```

To try it without a key, add `--sim` to use ideal gates and make no API calls.

## How it works

1. **The gate.** One request carries four Noul questions, one per input case. Jev returns P(yes). `p ≥ 0.5` is AND = 1, and inverting that gives NAND.
2. **The circuit.** `build_json.py` builds the entire machine out of NAND only: instruction decoder, 8-bit ripple-carry ALU (add/sub), carry and zero flags, PC incrementer, jump mux, 16-byte RAM read/write. It writes the result to `cpu.json` as a netlist.
3. **The runner.** `jev_run.py` knows nothing about CPUs. It holds a bit vector, evaluates the netlist level by level using Jev's answers, and latches the next state on each clock tick. Swap the JSON and it runs a different machine.
4. **Two modes.**
   - *fast* (default): Jev answers the 4 cases once per run, and every gate uses those answers.
   - *live*: every level of the circuit re-asks Jev on every clock cycle. That's 59 requests per cycle and about 49 s per cycle. It's slow, but it shows Jev really is in the loop.

### Instruction set

8-bit accumulator machine with 16 bytes of RAM. Each instruction byte is `[opcode:4][address:4]`.

| Op | Code | Meaning |
|---|---|---|
| `NOP` | 0 | do nothing |
| `LDA n` | 1 | A = RAM[n] |
| `ADD n` | 2 | A = A + RAM[n], set carry |
| `SUB n` | 3 | A = A − RAM[n], carry = no borrow |
| `STA n` | 4 | RAM[n] = A |
| `LDI n` | 5 | A = n (0–15) |
| `JMP n` | 6 | jump to n |
| `JZ n` | 7 | jump if A = 0 |
| `JC n` | 8 | jump if carry |
| `OUT` | 9 | output A |
| `HLT` | 15 | halt |

## What Jev does and what code does

| Jev: all the logic | Code: no logic |
|---|---|
| Decides what a gate outputs (the NAND truth table) | Holds register and RAM bits (flip-flops) |
| Every add, subtract, carry, decode, jump and RAM access comes from those answers through 2,102 gates | Ticks the clock and routes bits along the wires in `cpu.json` |
| If Jev answered wrong, the machine would compute wrong. There is no fallback. | Converts bits to decimal for display |

Jev is stateless and can't call itself, so something has to hold the bits and drive the clock. That's the same reason transistors need a circuit board.

## Files

| File | What it is |
|---|---|
| `cpu.json` | The whole machine as a NAND netlist (generated) |
| `jev_run.py` | Generic circuit runner: Jev gate calls, clock, token accounting, `--selftest` |
| `jev_calc.py` | Tkinter calculator whose arithmetic runs on the Jev CPU, with a "How it worked" panel |
| `build_json.py` | Compiles CPU + RAM to `cpu.json` and the programs to `programs/*.json` |
| `jev_computer.py` | The first version: netlist builder, CPU core, reference ISA checker (`--sim --check`) |
| `demo_template.html`, `build_demo.py`, `recorded_call.json` | The browser demo (`docs/index.html`, served by GitHub Pages) |

## What this is (and isn't)

This is not a practical computer. It runs about ten billion times slower than a real CPU, and nobody should do arithmetic through a language model. What it shows:

- **Narrow, single-step judgments are extremely reliable.** The gate question gets 0.99 / 0.01 every time. Multi-step questions ("what's the carry of 7+8?") were noticeably less sharp in earlier experiments.
- **Packing questions that share one state into a single request cuts cost by about 66%.** That's 446 tokens instead of 1,304 for 4 separate requests.
- **Code owns state and control flow, and the model supplies judgments.** That split is the right architecture for real Jev applications too.

---

### ภาษาไทย

คอมพิวเตอร์ 8-bit ที่ logic ทั้งหมดมาจากคำตอบของ Jev ต่อคำถามเดียว คือ "a กับ b เป็น 1 ทั้งคู่ไหม?" ผ่าน API ครั้งเดียว (446 tokens) คำตอบนั้นกลายเป็น NAND gate 2,102 ตัว ซึ่งประกอบเป็น CPU + RAM ที่รัน Fibonacci และบวก ลบ คูณ หารได้ ลองเล่นได้ที่ [หน้า demo](https://riwriwara.github.io/jev-computer/) โดยไม่ต้องใช้ API key

## License

MIT
