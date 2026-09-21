#!/usr/bin/env python3
"""build_demo.py — inject cpu.json, the recorded Jev call and the programs into
demo_template.html -> docs/index.html (a single self-contained page, served by GitHub Pages)."""
import json
import os

from jev_calc import PROGRAMS as CALC
from jev_computer import PROGRAMS

page = open("demo_template.html").read()
data = {
    "CPU": json.load(open("cpu.json")),
    "CALL": json.load(open("recorded_call.json")),
    "PROGRAMS": {**{k: v for k, v in PROGRAMS.items() if k != "mul"}, "calc": CALC},
}
for key, value in data.items():
    page = page.replace(f"/*__{key}__*/null", json.dumps(value, separators=(",", ":"), ensure_ascii=False))
page = ('<!doctype html>\n<html lang="th">\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n' + page)
os.makedirs("docs", exist_ok=True)
open("docs/index.html", "w").write(page)
print(f"docs/index.html: {len(page.encode()) / 1024:.0f} KB")
