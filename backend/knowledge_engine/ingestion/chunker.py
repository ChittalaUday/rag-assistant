"""Structure-aware chunking.

Chunks are exact slices of the normalised document text, so every chunk has verifiable character
offsets (evidence spans). Boundaries prefer, in order: a markdown heading, a sentence/line end, a
word boundary. Each chunk also records the heading path it sits under and, when a markdown table
was split, that table's header row, so retrieval keeps their context.
"""

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

# sentence ends in Latin and Indian scripts: . ! ? the danda (। ॥) and the Urdu full stop and
# question mark (۔ ؟); a line break also ends a unit (lists, table rows)
_SENTENCE_END = re.compile(r"[.!?\u0964\u0965\u06d4\u061f](?:\s+|$)|\n")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(\S.*)$")  # closing hashes and spaces are trimmed in code
# a sentence that opens with one of these refers back to the one before it: keep them together.
# English and a few frequent Indian-language pronouns/demonstratives; similarity covers the rest.
_REFERS_BACK = re.compile(
    r"^\W*(he|she|it|its|they|them|their|his|her|this|that|these|those|such|both|the former|"
    r"the latter|वह|यह|वे|ये|उन्होंने|उसने|इसके|उसके|इसकी|उसकी|इसे|उसे|তিনি|এটি|সেটি|তারা|"
    r"এই|সেই|அவர்|இது|அது|இவை|அவை|ఆయన|ఆమె|ఇది|అది|ಅವರು|ಇದು|ಅದು|അദ്ദേഹം|ഇത്|അത്)\b",
    re.IGNORECASE,
)
LENGTH_WEIGHT = 0.3  # how much a later cut is preferred when topic changes are alike


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


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of every sentence or line, in order; the boundaries the chunker may cut at."""
    spans, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        if text[start : m.end()].strip():
            spans.append((start, m.end()))
        start = m.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def refers_back(sentence: str) -> bool:
    return _REFERS_BACK.match(sentence) is not None


def chunk_document(
    text: str,
    max_chars: int = 700,
    min_chars: int = 200,
    overlap_chars: int = 180,
    break_scores: dict[int, float] | None = None,
) -> list[Chunk]:
    """Cut `text` into exact slices of at most `max_chars`.

    break_scores (hybrid chunking) maps a sentence end offset to how good a cut it is: about
    1 - similarity of the sentences on either side (a topic change), negative where the next
    sentence refers back. Among the sentence ends that fit, the best score wins, with a mild
    preference for longer chunks; without scores the last sentence end that fits is taken."""
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
        lo = bisect_left(h_starts, start + min_chars)
        hi = bisect_right(h_starts, limit)
        if lo < hi:  # keep sections apart, also in the document's last window
            end, at_heading = h_starts[lo], True
        elif limit < n:
            i, j = bisect_left(ends, start + min_chars), bisect_right(ends, limit)
            if i < j and break_scores:
                end = max(
                    ends[i:j],
                    key=lambda x: (
                        break_scores.get(x, 0.0) + LENGTH_WEIGHT * (x - start) / max_chars,
                        x,
                    ),
                )
            elif i < j:
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
    # a tiny tail is folded into the previous chunk rather than becoming an orphan, unless that
    # would exceed the size limit or the tail is a section of its own (it starts at a heading)
    if len(pieces) > 1:
        (prev_start, _), (tail_start, tail_end) = pieces[-2], pieces[-1]
        if (
            tail_end - tail_start < min_chars // 2
            and tail_end - prev_start <= max_chars
            and tail_start not in h_starts
        ):
            pieces[-2] = (prev_start, tail_end)
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


def split_chunk(text: str, chunk: Chunk, parts: int) -> list[Chunk]:
    """Split a chunk into about `parts` pieces of similar length, cutting at sentence ends (or word
    boundaries when a sentence is longer than a piece). Pieces are exact slices of `text` with the
    chunk's heading and table header; positions are renumbered by the caller."""
    if parts < 2 or chunk.char_end - chunk.char_start < 2:
        return [chunk]
    s, e = chunk.char_start, chunk.char_end
    ends = [s + m.end() for m in _SENTENCE_END.finditer(text[s:e]) if s + m.end() < e]
    target = (e - s) / parts
    cuts: list[int] = []
    for k in range(1, parts):
        goal = s + round(k * target)
        candidates = [x for x in ends if cuts[-1:] == [] or x > cuts[-1]] if ends else []
        near = min(candidates, key=lambda x: abs(x - goal), default=None)
        if near is None or abs(near - goal) > target / 2:  # no sentence end nearby: a word boundary
            space = text.rfind(" ", (cuts[-1] if cuts else s) + 1, goal)
            near = space + 1 if space > s else goal
        if near > (cuts[-1] if cuts else s) and near < e:
            cuts.append(near)
    bounds = [s, *cuts, e]
    return [
        Chunk(chunk.position, text[a:b], a, b, chunk.heading, chunk.table_header)
        for a, b in zip(bounds, bounds[1:], strict=False)
        if text[a:b].strip()
    ]
