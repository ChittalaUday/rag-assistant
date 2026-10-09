"""Fetch long, multi-section public documents for the chunking benchmark (dev tooling only).

Wikipedia articles (CC BY-SA 4.0) about Indian institutions and public schemes, in English and in
Hindi, Tamil and Bengali, saved as markdown (sections become headings) under work/chunk-bench/docs,
which Git ignores. The engine itself never downloads anything; this only builds an
evaluation corpus.

    uv run --no-sync python scripts/fetch_chunking_corpus.py
"""

import json
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "work" / "chunk-bench" / "docs"
MAX_CHARS = 15000  # long enough for many chunks, short enough to benchmark quickly
TITLES = {
    "en": [
        "Pradhan Mantri Fasal Bima Yojana", "Indian Space Research Organisation",
        "Reserve Bank of India", "Unified Payments Interface", "Aadhaar", "Indian Railways",
        "Goods and Services Tax (India)", "Right to Information Act, 2005",
        "Ayushman Bharat Yojana", "Digital India", "Swachh Bharat Mission",
        "National Education Policy 2020",
        "Mahatma Gandhi National Rural Employment Guarantee Act, 2005",
        "Green Revolution in India", "Monsoon of South Asia", "Tata Group", "Infosys", "Kerala",
        "Indian Institutes of Technology", "Election Commission of India",
        "Pradhan Mantri Jan Dhan Yojana", "Soil Health Card Scheme", "Kisan Credit Card",
        "National Food Security Act, 2013",
    ],
    "hi": [
        "भारतीय अंतरिक्ष अनुसंधान संगठन", "भारतीय रिज़र्व बैंक", "आधार (पहचान पत्र)", "भारतीय रेल",
        "सूचना का अधिकार अधिनियम", "प्रधानमंत्री जन धन योजना", "हरित क्रांति",
        "महात्मा गांधी राष्ट्रीय ग्रामीण रोजगार गारंटी अधिनियम",
    ],
    "ta": ["இந்திய விண்வெளி ஆய்வு நிறுவனம்", "இந்திய ரிசர்வ் வங்கி", "தமிழ்நாடு", "இந்திய இரயில்வே"],
    "bn": ["ভারতীয় মহাকাশ গবেষণা সংস্থা", "ভারতীয় রিজার্ভ ব্যাংক", "পশ্চিমবঙ্গ", "ভারতীয় রেল"],
}  # fmt: skip


def to_markdown(title: str, text: str) -> str:
    """Wikipedia plain-text extract -> markdown: "== Section ==" lines become headings."""
    text = re.sub(
        r"^(={2,6})\s*(.+?)\s*\1\s*$",
        lambda m: "#" * len(m[1]) + " " + m[2],
        text,
        flags=re.M,
    )
    body = re.sub(r"\n{3,}", "\n\n", text).strip()
    return f"# {title}\n\n{body}\n"


def fetch(lang: str, title: str) -> str | None:
    for wait in (2, 5, 15, 30):  # be polite: Wikipedia rate-limits bursts (429)
        time.sleep(wait / 2)
        response = _get(lang, title)
        if response.status_code != 429:
            break
        time.sleep(wait)
    response.raise_for_status()
    pages = response.json()["query"]["pages"]
    page = next(iter(pages.values()))
    text = page.get("extract") or ""
    if len(text) < 2000:
        return None  # missing or a stub: not useful for chunking
    cut = text[:MAX_CHARS]
    return to_markdown(page["title"], cut[: cut.rfind("\n") if "\n" in cut else len(cut)])


def _get(lang: str, title: str) -> httpx.Response:
    return httpx.get(
        f"https://{lang}.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "redirects": 1,
            "exsectionformat": "wiki",
            "format": "json",
            "titles": title,
        },  # fmt: skip
        headers={"User-Agent": "knowledge-engine-chunking-benchmark/1.0 (local evaluation)"},
        timeout=30,
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    kept, sources = 0, {}
    for lang, titles in TITLES.items():
        for title in titles:
            name = f"wiki-{lang}-" + re.sub(r"\W+", "-", title.casefold()).strip("-")[:60] + ".md"
            sources[name] = f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}"
            if (OUT / name).exists():
                kept += 1
                continue
            doc = fetch(lang, title)
            if doc is None:
                print(f"skip {lang}: {title}")
                sources.pop(name)
                continue
            (OUT / name).write_text(doc, encoding="utf-8")
            kept += 1
    (OUT.parent / "sources.json").write_text(json.dumps(sources, ensure_ascii=False, indent=1))
    print(f"{kept} documents in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
