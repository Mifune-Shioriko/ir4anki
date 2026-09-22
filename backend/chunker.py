#!/usr/bin/env python3
"""Heading-based markdown chunker for the notes corpus.

Each chunk = one heading section (heading line up to the next heading of
ANY level), with its breadcrumb path (e.g. ["第一节 概述", "一、浅层结构"]).
Long sections are split at paragraph boundaries; each part keeps the same
heading path so the frontend can still scroll+flash the right section.

Chunk identity = (file path, 1-based line_start) — stable across syncs as
long as the file doesn't change; a changed file is re-chunked wholesale.
"""

import hashlib
import re
from pathlib import Path

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

MAX_CHUNK_CHARS = 1500  # split longer sections at paragraph boundaries
MIN_CHUNK_CHARS = 20    # drop sections that are just a heading + whitespace


def _split_long(text_lines: list[tuple[int, str]], max_chars: int) -> list[list[tuple[int, str]]]:
    """Split (lineno, line) pairs into parts of <= max_chars at blank lines.

    Falls back to hard line boundaries when a single paragraph is huge
    (e.g. a long table with no blank lines).
    """
    parts: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, str]] = []
    cur_len = 0
    for lineno, line in text_lines:
        cur.append((lineno, line))
        cur_len += len(line) + 1
        if cur_len >= max_chars and line.strip() == "":
            parts.append(cur)
            cur = []
            cur_len = 0
    if cur:
        # tail: if the last part is big and has blank lines we already split;
        # otherwise accept one oversize chunk (tables) — better whole than cut
        parts.append(cur)

    # hard-split any oversize part at line boundaries
    out: list[list[tuple[int, str]]] = []
    for part in parts:
        size = sum(len(l) + 1 for _, l in part)
        if size <= max_chars * 1.6:  # tolerate a bit of overshoot
            out.append(part)
            continue
        buf: list[tuple[int, str]] = []
        blen = 0
        for lineno, line in part:
            buf.append((lineno, line))
            blen += len(line) + 1
            if blen >= max_chars:
                out.append(buf)
                buf = []
                blen = 0
        if buf:
            out.append(buf)
    return out


def chunk_markdown(text: str, rel_path: str, year: int | None) -> list[dict]:
    """Split one markdown file into heading-section chunks.

    Returns dicts: {chunk_id, file, year, title, heading_path (list[str]),
                    line_start, line_end (1-based inclusive), text}
    """
    lines = text.split("\n")
    sections: list[dict] = []
    stack: list[tuple[int, str]] = []  # (level, title) breadcrumbs
    cur: dict | None = None

    def flush():
        nonlocal cur
        if cur is not None:
            sections.append(cur)
        cur = None

    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            cur = {
                "title": title,
                "heading_path": [t for _, t in stack],
                "line_start": i + 1,
                "lines": [(i + 1, line)],
            }
        else:
            if cur is None:
                # preamble before the first heading (quotes/frontmatter-ish)
                cur = {
                    "title": None,
                    "heading_path": [],
                    "line_start": i + 1,
                    "lines": [],
                }
            cur["lines"].append((i + 1, line))
    flush()

    chunks: list[dict] = []
    for sec in sections:
        body = "\n".join(l for _, l in sec["lines"])
        stripped = re.sub(r"^#{1,6}\s+.*$", "", body, flags=re.M).strip()
        if len(stripped) < MIN_CHUNK_CHARS and not sec["heading_path"]:
            continue  # empty preamble / whitespace-only section
        line_end = sec["lines"][-1][0] if sec["lines"] else sec["line_start"]

        parts = _split_long(sec["lines"], MAX_CHUNK_CHARS) if len(body) > MAX_CHUNK_CHARS else [sec["lines"]]
        for pi, part in enumerate(parts):
            part_text = "\n".join(l for _, l in part).strip()
            if len(part_text.strip("# \n")) < MIN_CHUNK_CHARS and pi > 0:
                continue
            breadcrumb = " > ".join(sec["heading_path"])
            if breadcrumb:
                embed_text = f"{breadcrumb}\n{part_text}"
            else:
                embed_text = part_text
            p_start = part[0][0]
            p_end = part[-1][0]
            chunk_id = hashlib.sha1(f"{rel_path}:{p_start}".encode()).hexdigest()[:16]
            chunks.append({
                "chunk_id": chunk_id,
                "file": rel_path,
                "year": year,
                "title": sec["title"] or Path(rel_path).stem,
                "heading_path": sec["heading_path"],
                "line_start": p_start,
                "line_end": p_end,
                "text": part_text,
                "embed_text": embed_text,
            })
    return chunks


def file_year(rel_path: str) -> int | None:
    """Chronological anchor: the first path component when it looks like a year.

    ~/anki-notes/2026/局部解剖学/颈部.md → 2026. Files directly under the root
    have no year anchor (None → never year-filtered).
    """
    first = Path(rel_path).parts[0] if Path(rel_path).parts else ""
    return int(first) if re.fullmatch(r"\d{4}", first) else None


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
