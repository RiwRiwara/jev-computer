# jev-computer

**An 8-bit computer built from one yes/no question asked to an AI.**

[ภาษาไทย → README.th.md](README.th.md)

Jev is asked a single thing: *"Are both `a` and `b` equal to 1?"* Its four answers become a NAND gate, and 24,511 of those gates make a working computer with 256 bytes of RAM. It runs factorial, prime testing and bubble sort, and programs are plain JSON files.

![From one Jev API call to a computer: NAND truth table, full adder, 8-bit ALU, then a CPU with 256 bytes of RAM made of 24,511 NAND gates](images/overview.svg)

## What is Jev?

[Jev](https://docs.typesafe.ai) is TypeSafe's System One model. It doesn't write text. You send it some **state** and typed **questions**, and it sends back an answer for each question:

- **Noul:** a yes/no question; the answer is the probability of yes.
- **Choice:** pick one option from a list.
- **Score:** rate something on a scale you define.

A typical use, from the TypeSafe docs, is deciding whether a support message is urgent (response trimmed):

```json
{
  "model": "jev-latest",
  "state": "Help! My payouts have been failing for 3 days.",
  "questions": {
    "is_urgent": {"type": "noul", "instructions": "Does this convey urgency?"}
  }
}
```

```json
{"answers": {"is_urgent": {"type": "noul", "noul": 0.95}}}
```

Your code then decides what 0.95 means, for example "send it to the on-call team".

This project uses the same API with the smallest possible question. One request asks all four gate cases at once. This is the real request and response, with the response trimmed:

```json
{
  "model": "jev-1.13.0",
  "state": {"g00": {"a": 0, "b": 0}, "g01": {"a": 0, "b": 1},
            "g10": {"a": 1, "b": 0}, "g11": {"a": 1, "b": 1}},
  "questions": {
    "g00": {"type": "noul", "instructions": "Are both `g00.a` and `g00.b` equal to 1?"},
    "g01": {"type": "noul", "instructions": "Are both `g01.a` and `g01.b` equal to 1?"},
    "g10": {"type": "noul", "instructions": "Are both `g10.a` and `g10.b` equal to 1?"},
    "g11": {"type": "noul", "instructions": "Are both `g11.a` and `g11.b` equal to 1?"}
  }
}
```

```json
{"answers": {"g00": {"noul": 0.0}, "g01": {"noul": 0.01}, "g10": {"noul": 0.01}, "g11": {"noul": 0.99}},
 "usage": {"input_tokens": 446, "output_tokens": 76}}
```

**Jev works best when** each question is one narrow judgment, several questions about the same state share one request, and code combines the answers. This project takes that idea to the extreme.

## The idea: start from 0 and 1

It didn't start as a computer. It started as a question: **can Jev add numbers?**

1. **Asking directly wobbles.** One digit of `7 + 8` came back right with 98% confidence, but the carry ("does this column carry 1?") came back at only 88%, for a sum as easy as 15. One wrong carry spreads into every column after it.
2. **So shrink the question until it can't be ambiguous.** The smallest question in computing is about two bits: *"Are both `a` and `b` equal to 1?"* There are only four possible inputs, so all four can be tested. Jev answers 0.99 for (1, 1) and 0.00–0.01 for the other three, every time.
3. **That answer is a logic gate.** "Both are 1" is AND. Flip it and you get NAND, and NAND is *universal*: every digital circuit, including a whole computer, can be built from NAND alone.
4. **Then build back up, one layer at a time:**

| Layer | Built from | Size |
|---|---|---|
| One question to Jev | 4 yes/no answers | 1 API request, 446 tokens |
| NAND gate | Jev's answers | 1 gate |
| Full adder (adds 3 bits) | NAND gates | 9 gates |
| 8-bit adder | full adders | 72 gates |
| CPU: decoder, ALU, flags, program counter | adders and gates | 383 gates |
| Whole machine: CPU + 256 bytes of RAM | all of the above | 24,511 gates |
| Programs: factorial, prime, bubble sort | instructions in RAM | JSON files |

The first try asked for NAND directly. It worked, but the machine now asks for AND and inverts it, because negated questions ("Is it false that…") make Noul less sharp:

![First try: asking NAND directly gave 2% true for a=1, b=1](images/gate-first-try.svg)

![The gate the machine uses: all four cases in one request, 0%, 1%, 1%, 99%](images/gate-truth-table.svg)

## Quick start

Python 3.9+, standard library only.

```bash
cp .env.example .env                                  # add your TypeSafe API key

python jev_run.py --selftest                          # check Jev's gate answers (5 requests)
python jev_run.py programs/factorial.json n=5         # 120
python jev_run.py programs/prime.json n=97            # 1 (prime)
python jev_run.py programs/bubble_sort.json list=5,2,9,1,7,3
```

![Sample run of add.json a=40 b=3 with --trace: answer 43, 1 Jev request, 446 tokens, $0.000019, 0.81 s, 5 clock cycles](images/sample-add.svg)

| Option | What it does |
|---|---|
| `name=value` | Set one of the program's inputs (0–255) |
| `name=4,7,9` | Set a list input |
| `--trace` | Print every instruction the CPU runs |
| `--test` | Run the tests written inside the program's JSON |
| `--selftest` | Ask Jev the gate question 5 times and check every answer |

## Programs

| File | Does | Inputs | Example |
|---|---|---|---|
| `add.json` | a + b (up to 510) | `a`, `b` | `a=255 b=255` → 510 |
| `sub.json` | a − b (can be negative) | `a`, `b` | `a=15 b=17` → −2 |
| `mul.json` | a × b | `a`, `b` | `a=7 b=6` → 42 |
| `div.json` | a ÷ b (whole number) | `a`, `b` | `a=200 b=7` → 28 |
| `power.json` | x to the power y | `x`, `y` | `x=3 y=5` → 243 |
| `pow2.json` | 2 to the power y | `y` | `y=7` → 128 |
| `factorial.json` | n! | `n` | `n=5` → 120 |
| `sum_to_n.json` | 1 + 2 + … + n | `n` | `n=10` → 55 |
| `gcd.json` | Greatest common divisor | `a`, `b` (1–255) | `a=48 b=18` → 6 |
| `prime.json` | Is n prime? (1 = yes, 0 = no) | `n` | `n=251` → 1 |
| `max_min.json` | Largest and smallest of 8 numbers | `list` (8) | `list=42,7,19,200,3,88,150,64` → [200, 3] |
| `linear_search.json` | Position of `x` in 8 numbers | `list` (8), `x` | `x=42` → 5 |
| `bubble_sort.json` | Sort 6 numbers | `list` (6) | `list=5,2,9,1,7,3` → [1, 2, 3, 5, 7, 9] |
| `countdown.json` | n, n−1, … 0 | `n` | `n=5` → 5 4 3 2 1 0 |
| `fib.json` | Fibonacci up to 144 | — | 0 1 1 2 … 144 |

All files are in `programs/`.

**Tips:**
- **Numbers are 8-bit (0–255).** Results over 255 from `mul`, `power`, `pow2`, `factorial` and `sum_to_n` answer `overflow (> 255)`. Division gives the whole-number quotient.
- **Never divide by 0.** The CPU would loop forever; the runner stops after 100,000 clock cycles with an error. `gcd` needs both numbers to be 1 or more for the same reason.
- **Put the smaller number in `b` for `mul`.** `b` is the loop count: `a=200 b=1` takes 14 clock cycles, `a=1 b=200` takes 1,805.

## Results (real runs against `jev-1.13.0`)

![Sample runs of 8 programs on real Jev: every run is 1 request, 446 tokens, $0.000019; time ranges from 0.78 s to 4.48 s](images/results.svg)

- **Every run costs the same:** 1 request, 446 tokens, about $0.00002, because Jev answers the gate once and every gate reuses it.
- **Time is mostly waiting on Jev** (about 0.7–0.9 s). Running the gates locally only matters for long programs like `prime n=251`.
- **Tests:** all 65 tests across the 15 programs pass (65 requests, 28,990 tokens), and the gate selftest is 20/20.

## Write your own program

A program is one JSON file. Nothing else needs to change.

```json
{
  "about": "Double a number: a + a.",
  "inputs": ["a"],
  "code": ["LDA a", "ADD a", "OUT", "HLT"],
  "data": {"a": 21},
  "tests": [
    {"with": {"a": 21}, "expect": [42]},
    {"with": {"a": 100}, "expect": [200]}
  ]
}
```

```bash
python jev_run.py double.json a=21     # answer: [42]
python jev_run.py double.json --test   # 2/2 passed
```

| Key | Meaning |
|---|---|
| `code` | Instructions, in order. Start a line with `name:` to label it, for example `"loop: LDA n"`. |
| `data` | Named variables, `{"n": 5}`, or lists, `{"list": [4, 7, 9]}`. They are placed in RAM right after the code. |
| `inputs` | Which `data` names can be set from the command line or by tests |
| `about` | One line printed before running |
| `result` | *Optional.* How to read the output port (see below) |
| `tests` | *Optional.* `{"with": {inputs}, "expect": answer}` cases, used by `--test` |

An operand can be a number (`LDI 5`), a label or data name (`JMP loop`, `LDA n`), or a name plus or minus a number (`LDA list+2`, `STA get+1`). With `LDI`, a name gives its address, so `LDI list` puts the list's address into A. There are no pointer instructions, so programs that walk a list (like `bubble_sort`) rewrite their own `LDA`/`STA` operands.

`result` options:
- Leave it out to get the list of every `OUT`.
- `{}` means the first `OUT` is the answer.
- `"add": n` adds n to the answer.
- `"second_output_adds": 256` adds 256 if there is a second `OUT` (a carry flag).
- `"no_output": "text"` is the answer when nothing was output.

Rules: code and data share 256 bytes of RAM (each instruction takes 2). Values are 0–255. The program must reach `HLT`.

### Instruction set

8-bit accumulator machine with 256 bytes of RAM. Every instruction is 2 bytes, `[opcode] [operand]`.

| Instruction | Meaning |
|---|---|
| `LDA n` | A = RAM[n] |
| `ADD n` | A = A + RAM[n], carry = 1 if it went over 255 |
| `SUB n` | A = A − RAM[n], carry = 1 if **no** borrow |
| `STA n` | RAM[n] = A |
| `LDI n` | A = n (0–255) |
| `JMP n` | jump to address n |
| `JZ n` | jump if A = 0 |
| `JC n` | jump if carry = 1 |
| `OUT` | send A to the output port |
| `HLT` | stop |
| `NOP` | do nothing |

## How it works

1. **The circuit.** `build_cpu.py` builds the whole machine out of NAND only (decoder, ALU, flags, program counter, jumps, and 256 bytes of RAM) and writes it to `cpu.json`.
2. **The runner.** `jev_run.py` loads a program into RAM, asks Jev the 4 gate cases in one request, then evaluates all 24,511 gates on every clock cycle using Jev's answers until the program halts.

![Inside the CPU: PC, RAM, instruction decoder, ALU, register A, carry flag, next PC and output port](images/cpu-inside.svg)

![One run: jev_run.py asks Jev once, then evaluates 24,511 gates on every clock cycle until HLT](images/one-run.svg)

**Is it really built from Jev?** Yes, the way a real computer is built from transistors: transistors decide what each gate outputs, but still need a circuit board, wires and a clock. Here Jev decides what every gate outputs; `cpu.json` is the circuit board and `jev_run.py` is the wires and the clock.

| Jev: all the logic | Code: no logic |
|---|---|
| Decides what a gate outputs (the NAND truth table) | Holds register and RAM bits (flip-flops) |
| Every add, carry, decode, jump and RAM access comes from those answers | Ticks the clock and routes bits along the wires in `cpu.json` |
| If Jev answered one case wrong, every program would compute wrong. There is no fallback. | Converts bits to decimal for display |

Jev answers the 4 cases once per run, and those answers drive every gate on every clock cycle. Jev defines the gate; it doesn't flip each switch itself.

## Files

| File | What it is | When you need it |
|---|---|---|
| `jev_run.py` | The runner: loads a program, asks Jev once, runs the clock | Every time you run something |
| `programs/*.json` | The 15 programs | Add a file here to add a program |
| `cpu.json` | The machine: 24,511 NAND gates and the instruction set (generated) | Read by `jev_run.py` |
| `build_cpu.py` | Builds `cpu.json`; `--check` verifies it without the API | Only if you change the machine's design |

## What this is (and isn't)

This is not a practical computer: it runs about ten billion times slower than a real CPU, and nobody should do arithmetic through a language model. It's a demonstration of how to use a model like Jev well: **don't ask one big question it has to reason through. Break the work into the smallest judgments it can answer sharply, and let code combine them.**

## License

MIT
