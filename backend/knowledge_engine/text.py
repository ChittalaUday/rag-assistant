"""Pure text helpers: document normalisation and tokenising.

Nothing here knows about any domain; it is shared by ingestion and retrieval so documents and
questions are normalised the same way.
"""

import html
import re
import sys
import unicodedata

# Closed-class English words. Lexical overlap on these inflates irrelevant chunks
# past chunks that match on content terms. ponytail: static list, swap for IDF
# if the corpus grows past hand-tuned stopwords.
# fmt: off
STOPWORDS = frozenset([
    "a", "an", "the", "of", "is", "are", "was", "were", "be", "been", "being", "am", "to", "from",
    "in", "on", "at", "by", "for", "with", "as", "and", "or", "but", "not", "no", "that", "this",
    "it", "its", "their", "they", "them", "he", "she", "him", "her", "his", "hers", "we", "us",
    "our", "you", "your", "i", "my", "me", "mine", "do", "does", "did", "can", "could", "will",
    "would", "should", "may", "might", "have", "has", "had", "so", "than", "then", "there",
    "here", "such", "any", "some", "all", "both", "each", "if", "while", "about", "into", "over",
    "under", "more", "most", "less", "very", "just", "also", "only", "own", "same", "like", "now",
    "how", "why", "when", "where", "what", "which", "whom", "whose", "who", "doesn", "isn",
    "wasn", "don", "didn", "aren", "weren", "hasn", "haven", "hadn", "won", "shouldn", "couldn",
    "wouldn", "mustn", "mightn", "needn",
])
# fmt: on


def _strip_comments(text: str) -> str:
    """Remove <!-- ... --> in one pass; an unclosed marker (and so everything after it) stays."""
    out: list[str] = []
    i = 0
    while (start := text.find("<!--", i)) >= 0:
        end = text.find("-->", start + 4)
        if end < 0:
            break
        out.append(text[i:start])
        i = end + 3
    out.append(text[i:])
    return "".join(out)


def normalize_text(text: str) -> str:
    """Canonical document text: entities decoded, markup noise and table padding removed.

    Chunks, quotes and offsets all refer to this normalised text, so it is stored as the
    document's text of record. Plain prose passes through unchanged.
    """
    text = html.unescape(text)  # Docling exports "&" as "&amp;"
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace(" ", " ").replace("​", "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = _strip_comments(text)  # e.g. "<!-- image -->" placeholders
    lines: list[str] = []
    for line in text.split("\n"):
        line = line.rstrip(" \t")
        if line.lstrip().startswith("|"):
            line = _compact_table_row(line)
            if not line:
                continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))


def _compact_table_row(line: str) -> str:
    """Collapse padded markdown table cells; drop rows with no content at all."""
    cells = [re.sub(r"\s+", " ", c).strip() for c in line.strip().strip("|").split("|")]
    if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
        return "| " + " | ".join("---" for _ in cells) + " |"
    if not any(cells):
        return ""
    return "| " + " | ".join(cells) + " |"


def _mark_ranges() -> str:
    """Every combining mark (Unicode category M*) as a regex character class body."""
    marks = [c for c in range(0x300, sys.maxunicode + 1) if unicodedata.category(chr(c))[0] == "M"]
    ranges: list[str] = []
    start = prev = marks[0]
    for c in [*marks[1:], -1]:
        if c != prev + 1:
            ranges.append(
                re.escape(chr(start)) + ("-" + re.escape(chr(prev)) if prev > start else "")
            )
            start = c
        prev = c
    return "".join(ranges)


# A word: letters, digits and the combining marks inside them (Indic vowel signs, viramas, nukta;
# Arabic harakat) plus the zero-width (non-)joiners that shape Indic conjuncts. Python's \w alone
# would cut "हिन्दी" into "ह" and "नद".
_MARKS = _mark_ranges()
WORD = re.compile(rf"[\w{_MARKS}\u200c\u200d]+")
_LATIN = re.compile(r"[A-Za-z\u00c0-\u024f\u1e00-\u1eff]")


def words(text: str) -> list[str]:
    return WORD.findall(text)


def is_word(text: str) -> bool:
    return WORD.fullmatch(text) is not None


def is_latin(word: str) -> bool:
    return _LATIN.match(word) is not None


# Languages an answer can be asked for, with the script each is written in (used to check the
# answer came back in it). With none chosen, answers follow the language of the question.
ANSWER_LANGUAGES = {
    "English": "LATIN",
    "Hindi": "DEVANAGARI",
    "Marathi": "DEVANAGARI",
    "Bengali": "BENGALI",
    "Assamese": "BENGALI",
    "Odia": "ORIYA",
    "Punjabi": "GURMUKHI",
    "Gujarati": "GUJARATI",
    "Tamil": "TAMIL",
    "Telugu": "TELUGU",
    "Kannada": "KANNADA",
    "Malayalam": "MALAYALAM",
    "Urdu": "ARABIC",
}


def script_of(text: str) -> str:
    """The script most letters of the text are written in ("LATIN", "DEVANAGARI", "TAMIL", ...)."""
    counts: dict[str, int] = {}
    for c in text:
        if c.isalpha():
            name = unicodedata.name(c, "")
            script = name.split(" ", 1)[0] if name else ""
            counts[script] = counts.get(script, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else ""


def content_terms(text: str) -> set[str]:
    """Lower-cased word tokens with stopwords and single characters dropped."""
    return {t for t in words(text.casefold()) if t not in STOPWORDS and len(t) > 1}


def ordered_terms(text: str) -> list[str]:
    """Distinct content terms in first-seen order (stable queries and traces)."""
    seen: dict[str, None] = {}
    for t in words(text.casefold()):
        if t not in STOPWORDS and len(t) > 1:
            seen.setdefault(t)
    return list(seen)


def _singular(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def token_forms(text: str) -> list[str]:
    """Matching tokens: casefolded, '&' as 'and', plural-folded, and accent-folded for Latin letters
    only ("Cafés" -> "cafe"); the marks of other scripts are part of their letters and stay.

    Used on both sides of a term comparison; the forms are keys, never shown to users.
    """
    decomposed = unicodedata.normalize("NFKD", html.unescape(text))
    kept: list[str] = []
    base = ""
    for c in decomposed:
        if unicodedata.combining(c):
            if not _LATIN.match(base):
                kept.append(c)
        else:
            base = c
            kept.append(c)
    text = unicodedata.normalize("NFC", "".join(kept)).casefold().replace("&", " and ")
    return [_singular(t) for t in words(text)]


def clean_labels(labels: list[str] | None) -> list[str]:
    """Document labels as stored: lower-case [a-z0-9_-], at most 30 characters, distinct, sorted."""
    cleaned = {re.sub(r"[^a-z0-9_-]", "", x.strip().casefold())[:30] for x in labels or []}
    return sorted(c for c in cleaned if c)
