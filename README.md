# jev-computer

**An 8-bit computer built from one yes/no question asked to an AI.**

[Jev](https://docs.typesafe.ai) (TypeSafe's System One model) doesn't do arithmetic, doesn't know what a CPU is, and writes no code. It is asked a single thing through its API:

> *"Are both `a` and `b` equal to 1?"*

Its answers to the four possible inputs become an AND gate, which is inverted to NAND. NAND is a universal gate, so 2,102 of them wired together make a working computer that runs Fibonacci, counts down, and multiplies.

```
one Jev API call ──► NAND truth table ──► full adder (9 NANDs) ──► ALU ──► 8-bit CPU + RAM
   446 tokens          p = .00 .01 .01 .99       2,102 NAND gates, 59 levels deep
```

## Quick start

Python 3.9+, standard library only.

```bash
cp .env.example .env                                  # add your TypeSafe API key

python jev_run.py cpu.json --selftest                 # verify the gate: 5 requests
python jev_run.py cpu.json programs/fib.json          # run Fibonacci on the Jev CPU
python jev_run.py cpu.json programs/fib.json --live   # Jev answers every gate level (slow)
python jev_run.py cpu.json programs/fib.json --sim    # ideal gates, no API
```

```
   0 pc=  0 a=  0  jev_calls=1     0.8s
  ▶ 0
   ...
 124 pc= 10 a=121  jev_calls=1     0.9s
output: [0, 1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144]
1 Jev requests, 446 input / 76 output tokens
```

## Results (real runs against `jev-1.13.0`)

| Run | Result | Requests | Input tokens | Time |
|---|---|---:|---:|---:|
| Gate selftest, 5 repeats × 4 cases | 20/20 correct, p = 0.00–0.01 / 0.99 | 5 | 2,230 | — |
| Fibonacci (fast mode) | `0 1 1 2 3 5 8 13 21 34 55 89 144` | 1 | 446 | ~1 s |
| 40 + 3 (**live**: Jev asked at every circuit level, every clock) | `43` | 295 | 112,441 | 245 s |
| Countdown (**live**, older 1-case-per-request prompt) | `5 4 3 2 1 0` | 3,642 | not measured | 1,175 s |

The fast-mode Jev call costs about **$0.00002** (input $0.042 per million tokens).

## How it works

1. **The gate.** One request carries four Noul questions, one per input case. Jev returns P(yes). `p ≥ 0.5` is AND = 1, and inverting that gives NAND.
2. **The circuit.** `build_cpu.py` builds the whole machine out of NAND only: instruction decoder, 8-bit ripple-carry ALU (add/sub), carry and zero flags, PC incrementer, jump mux, 16-byte RAM read/write. It writes the result to `cpu.json` as a netlist.
3. **The runner.** `jev_run.py` knows nothing about CPUs. It holds a bit vector, evaluates the netlist level by level using Jev's answers, and latches the next state on each clock tick.
4. **Two modes.**
   - *fast* (default): Jev answers the 4 cases once per run, and every gate uses those answers.
   - *live*: every circuit level re-asks Jev on every clock cycle. That's 59 requests per cycle and about 49 s per cycle. It's slow, but it shows Jev really is in the loop.

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

A program is a JSON file of initial RAM bytes, `{"ram0": 29, "ram1": 144, ...}`, where each value is `opcode × 16 + address`. See `programs/` and `PROGRAMS` in `build_cpu.py`.

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
| `jev_run.py` | Runner: Jev gate calls, clock, token accounting, `--selftest`, `--live`, `--sim` |
| `cpu.json` | The whole machine as a NAND netlist (generated) |
| `programs/` | Fibonacci, countdown, 7 × 6 |
| `build_cpu.py` | Builds `cpu.json` and `programs/` from NAND gates; `--check` verifies it against the ISA |

## What this is (and isn't)

This is not a practical computer. It runs about ten billion times slower than a real CPU, and nobody should do arithmetic through a language model. What it shows:

- **Narrow, single-step judgments are extremely reliable.** The gate question gets 0.99 / 0.01 every time.
- **Packing questions that share one state into a single request cuts cost by about 66%.** That's 446 tokens instead of 1,304 for 4 separate requests.
- **Code owns state and control flow, and the model supplies judgments.** That split is the right architecture for real Jev applications too.

---

**ภาษาไทย:** คอมพิวเตอร์ 8-bit ที่ logic ทั้งหมดมาจากคำตอบของ Jev ต่อคำถามเดียว คือ "a กับ b เป็น 1 ทั้งคู่ไหม?" ผ่าน API ครั้งเดียว (446 tokens) คำตอบนั้นกลายเป็น NAND gate 2,102 ตัวที่ประกอบเป็น CPU + RAM รันด้วย `python jev_run.py cpu.json programs/fib.json`

## License

MIT
