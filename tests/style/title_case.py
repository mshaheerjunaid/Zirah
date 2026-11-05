#!/usr/bin/env python3
"""Title Case check for every piece of interface text in Zirah.

The rule, applied to the desktop app and to the OLED:
  * every word starts with a capital letter,
  * except short function words (a, an, the, and, or, for, of, to, in, on,
    at, by, it, with, ...), which stay lowercase unless they start the text
    or a new sentence,
  * words written fully in capitals (ZIRAH, READY, ML-DSA) stay that way,
  * unit symbols keep their SI case (ms, V, kHz, °C), and tokens with digits or
    placeholders (12V, P-256, {name}, %lu) are data, not words.

    python tests/style/title_case.py          check the OLED and desktop app
    python tests/style/title_case.py --list   also print every string checked

Sources: firmware/src/ui_text.h and oled_ui.c (the OLED), ui/strings.json
(all desktop copy) and any literal text handed to a widget in ui/*.py.
Exit status 1 if any string breaks the rule.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MINOR = {
    "a", "an", "the", "and", "but", "or", "nor", "for", "so", "as", "at", "by", "if",
    "in", "of", "on", "per", "to", "via", "vs", "it", "with", "from", "into",
}
UNITS = {"ms", "s", "us", "µs", "V", "mV", "kV", "Hz", "kHz", "MHz", "B", "KB", "MB",
         "GB", "kB", "bit", "bits", "x", "°C"}
SENTENCE_END = re.compile(r"[.!?:]$")


def word_ok(word: str, edge: bool) -> bool:
    if not word or not any(c.isalpha() for c in word):
        return True
    if word in UNITS:
        return True
    if any(c.isdigit() for c in word):
        return True
    letters = [c for c in word if c.isalpha()]
    if all(c.isupper() for c in letters) and (len(letters) > 1 or word in {"A", "I"}):
        return True
    lower = word.lower()
    if lower in MINOR:
        return word[0].isupper() if edge else word == lower
    return word[0].isupper()


def check(text: str) -> list:
    """Return the words of `text` that break the rule."""
    problems = []
    # Placeholders and format specifiers are data.
    text = re.sub(r"\{[^}]*\}|%[-+ #0-9.]*l{0,2}[a-zA-Z%]|\$\{[^}]*\}", " Value ", text)
    first = True
    raws = text.split()
    for n, raw in enumerate(raws):
        token = raw.strip("\"'()[]<>,;*")
        if not token:
            continue
        if re.search(r"\w\.\w", token) or any(c.isdigit() for c in token):
            first = False   # file names, versions and numbers are data
            continue
        # The first and the last word of a text or sentence are always
        # capitalised, even when they are short function words.
        last = n == len(raws) - 1 or bool(SENTENCE_END.search(token))
        parts = re.split(r"[-/]", token.rstrip(".!?:"))
        for i, part in enumerate(parts):
            part = part.strip("\"'()[]<>,;.!?:")
            if not part:
                continue
            edge = (first and i == 0) or (last and i == len(parts) - 1)
            if not word_ok(part, edge):
                problems.append(part)
        first = bool(SENTENCE_END.search(token)) or token in {"-", "|"}
    return problems


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

C_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')


def oled_strings():
    out = []
    hdr = ROOT / "firmware" / "src" / "ui_text.h"
    for m in re.finditer(r'#define\s+(TXT_\w+)\s+"((?:[^"\\]|\\.)*)"', hdr.read_text()):
        out.append((f"ui_text.h {m.group(1)}", m.group(2)))
    src = ROOT / "firmware" / "src" / "oled_ui.c"
    for n, line in enumerate(src.read_text().splitlines(), 1):
        if line.lstrip().startswith("#include"):
            continue
        for m in C_STRING.finditer(line):
            out.append((f"oled_ui.c:{n}", m.group(1)))
    return out


def desktop_strings():
    """All copy of the desktop app lives in ui/strings.json."""
    out = []
    data = json.loads((ROOT / "ui" / "strings.json").read_text(encoding="utf-8"))

    def walk(prefix, node):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(f"{prefix}[{i}]", v)
        elif isinstance(node, str):
            out.append((f"strings.json {prefix}", node))

    walk("", data)
    return out


def hardcoded_ui_text():
    """Text passed straight to a widget in ui/*.py instead of through T()."""
    out = []
    pat = re.compile(r'(?:label|Button|Toggle|Chip|setText|setWindowTitle|DropZone)\(\s*"([^"]*[A-Za-z][^"]*)"')
    for f in sorted((ROOT / "ui").glob("*.py")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for m in pat.finditer(line):
                text = re.sub(r"\\.", " ", m.group(1))   # escapes such as \\n are not words
                if any(c.isalpha() for c in text):
                    out.append((f"{f.name}:{n}", text))
    return out


def main() -> int:
    show = "--list" in sys.argv
    bad = 0
    items = oled_strings() + desktop_strings() + hardcoded_ui_text()
    for where, text in items:
        problems = check(text)
        if problems:
            bad += 1
            print(f"FAIL {where}: {text!r} -> {', '.join(problems)}")
        elif show:
            print(f"ok   {where}: {text!r}")
    print(f"{len(items)} strings checked, {bad} not in Title Case")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
