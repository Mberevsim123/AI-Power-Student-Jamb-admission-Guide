"""Normalise model Markdown into a clean, predictable house style.

Small models sometimes loop (the same table row or line dozens of times), wrap
tables without the blank lines GFM needs, or exceed sensible table sizes. These
fixes are structural only; they never change the wording of a claim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MAX_TABLE_ROWS = 15
ROW = re.compile(r"^\s*\|.*\|\s*$")
SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")


@dataclass
class Table:
    start: int  # index of the header line
    end: int  # index after the last row
    header: list = field(default_factory=list)
    align: str = ""  # the separator line, kept verbatim
    rows: list = field(default_factory=list)  # raw row lines


def cells(line):
    """Split a Markdown table row into stripped cell texts."""
    inner = line.strip()
    inner = inner[1:] if inner.startswith("|") else inner
    inner = inner[:-1] if inner.endswith("|") else inner
    return [c.strip() for c in re.split(r"(?<!\\)\|", inner)]


def find_tables(lines):
    tables, i = [], 0
    while i < len(lines) - 1:
        if ROW.match(lines[i]) and SEPARATOR.match(lines[i + 1]):
            j = i + 2
            while j < len(lines) and ROW.match(lines[j]):
                j += 1
            tables.append(Table(i, j, cells(lines[i]), lines[i + 1], lines[i + 2 : j]))
            i = j
        else:
            i += 1
    return tables


def _key(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def tidy(text):
    """Deduplicate looping rows/lines, cap table length and fix table spacing."""
    if not text:
        return text
    lines = text.replace("\r\n", "\n").split("\n")
    out, i = [], 0
    tables = {t.start: t for t in find_tables(lines)}
    while i < len(lines):
        table = tables.get(i)
        if table:
            seen, rows = set(), []
            for row in table.rows:
                key = _key("|".join(cells(row)))
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
            if out and out[-1].strip():
                out.append("")  # GFM needs a blank line before a table
            out += [lines[table.start], table.align, *rows[:MAX_TABLE_ROWS]]
            if len(rows) > MAX_TABLE_ROWS:
                out += ["", f"_Showing {MAX_TABLE_ROWS} of {len(rows)} rows._"]
            if table.end < len(lines) and lines[table.end].strip():
                out.append("")
            i = table.end
            continue
        line = lines[i]
        # A line repeated back-to-back is a generation loop, not emphasis.
        if line.strip() and out and _key(out[-1]) == _key(line):
            i += 1
            continue
        out.append(line)
        i += 1
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
