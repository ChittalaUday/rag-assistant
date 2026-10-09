"""Structure-aware chunking.

Chunks are exact slices of the normalised document text, so every chunk has verifiable character
offsets (evidence spans). Boundaries prefer, in order: a markdown heading, a sentence/line end, a
word boundary. Each chunk also records the heading path it sits under and, when a markdown table
was split, that table's header row, so retrieval keeps their context.
"""

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

_SENTENCE_END = re.compile(r"[.!?](?:\s+|$)|\n")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(\S.*)$")  # closing hashes and spaces are trimmed in code


@dataclass(frozen=True)
class Chunk:
    position: int
    text: str
    char_start: int
    char_end: int
    heading: str | None  # "Section › Subsection" the chunk's first line sits under
    table_header: str | None = None  # header row when the chunk starts inside a markdown table


def _headings(text: str) -> tuple[list[int], list[str], list[int]]:
    """(line starts, breadcrumb after each heading, line end offsets) for every markdown heading."""
    starts: list[int] = []
    ends: list[int] = []
    paths: list[str] = []
    stack: list[tuple[int, str]] = []
    offset = 0
    for line in text.split("\n"):
        m = _HEADING.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).rstrip(" \t").rstrip("#").rstrip(" \t") or m.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            starts.append(offset)
            ends.append(offset + len(line) + 1)
            paths.append(" › ".join(t for _, t in stack))
        offset += len(line) + 1
    return starts, paths, ends


def _table_header(text: str, start: int, firsts: dict[int, int] | None = None) -> str | None:
    """Header row of the markdown table the position is inside, if it is not the table's start.
    `firsts` remembers each line's table start so a long table is walked once, not per chunk."""
    firsts = {} if firsts is None else firsts
    line_start = text.rfind("\n", 0, start) + 1
    if not text.startswith("|", line_start):
        return None
    first = line_start
    while first > 0:
        if first in firsts:
            first = firsts[first]
            break
        prev_start = text.rfind("\n", 0, first - 1) + 1
        if not text.startswith("|", prev_start):
            break
        first = prev_start
    firsts[line_start] = first
    if first == line_start:
        return None  # the chunk begins at the table's own header
    return text[first : text.find("\n", first)].strip() or None


def chunk_document(
    text: str, max_chars: int = 700, min_chars: int = 200, overlap_chars: int = 180
) -> list[Chunk]:
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1")  # a zero-width chunk never advances
    n = len(text)
    table_starts: dict[int, int] = {}
    ends = [m.end() for m in _SENTENCE_END.finditer(text)]
    h_starts, h_paths, h_ends = _headings(text)
    pieces: list[tuple[int, int]] = []
    start = 0
    while start < n:
        limit = min(start + max_chars, n)
        end, at_heading = limit, False
        if limit < n:
            lo = bisect_left(h_starts, start + min_chars)
            hi = bisect_right(h_starts, limit)
            if lo < hi:  # keep a section together: cut right before the next heading
                end, at_heading = h_starts[lo], True
            else:
                i, j = bisect_left(ends, start + min_chars), bisect_right(ends, limit)
                if i < j:
                    end = ends[j - 1]
                else:
                    space = text.rfind(" ", start + min_chars, limit)
                    if space > start:
                        end = space + 1
            # never end on a heading line whose body would start the next chunk
            k = bisect_right(h_starts, end - 1) - 1
            if k >= 0 and h_starts[k] >= start and end <= h_ends[k] and h_starts[k] > start:
                end, at_heading = h_starts[k], True
        pieces.append((start, end))
        if end >= n:
            break
        if at_heading:
            start = end
            continue
        i, j = bisect_left(ends, max(start + 1, end - overlap_chars)), bisect_left(ends, end)
        if i < j:
            start = ends[j - 1]
        else:
            space = text.find(" ", max(start + 1, end - 90), end)
            start = space + 1 if space >= 0 else end
    # a tiny tail is folded into the previous chunk rather than becoming an orphan
    if len(pieces) > 1 and pieces[-1][1] - pieces[-1][0] < min_chars // 2:
        pieces[-2] = (pieces[-2][0], pieces[-1][1])
        pieces.pop()
    chunks: list[Chunk] = []
    for s, e in pieces:
        body = text[s:e]
        if not body.strip():
            continue
        first = s + len(body) - len(body.lstrip())
        # Any heading inside a chunk starts within its first min_chars (otherwise the chunk would
        # have been cut before it), so the last heading in that window names what the chunk is
        # about; this also resolves stacked headings such as a repeated PDF page header.
        k = bisect_right(h_starts, min(first + min_chars, e - 1)) - 1
        chunks.append(
            Chunk(
                position=len(chunks),
                text=body,
                char_start=s,
                char_end=e,
                heading=h_paths[k] if k >= 0 else None,
                table_header=_table_header(text, first, table_starts),
            )
        )
    return chunks
