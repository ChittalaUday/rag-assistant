"""Chunk storage and search: pgvector (dense) and full-text (sparse) retrieval, plus citations."""

from dataclasses import dataclass
from typing import Any

from knowledge_engine.errors import AppError
from knowledge_engine.provenance import source_urls
from knowledge_engine.store.base import Db, Row, vector_literal
from knowledge_engine.text import is_word


@dataclass
class ChunkRow:
    id: str
    position: int
    text: str
    char_start: int
    char_end: int
    heading: str | None
    context: str
    embedding: list[float]
    embedding_model: str


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance with adjacent transpositions (the commonest typing slip)."""
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cost = min(cost, prev2[j - 2] + 1)
            cur.append(cost)
        prev2, prev = prev, cur
    return prev[-1]


class ChunkRepo:
    def __init__(self, db: Db):
        self.db = db

    def replace(self, document_id: str, rows: list[ChunkRow]) -> None:
        """Swap a document's chunks (cascades to mentions/evidence). Call inside a transaction."""
        self.db.run("DELETE FROM chunks WHERE document_id=%s", (document_id,))
        self.db.many(
            "INSERT INTO chunks(id,document_id,position,text,char_start,char_end,heading,context,"
            "embedding,embedding_model) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,%s)",
            [
                (
                    r.id, document_id, r.position, r.text, r.char_start, r.char_end, r.heading,
                    r.context, vector_literal(r.embedding), r.embedding_model,
                )
                for r in rows
            ],
        )  # fmt: skip

    def for_document(self, document_id: str) -> list[Row]:
        return self.db.rows(
            "SELECT id, position, text, char_start, char_end, heading FROM chunks "
            "WHERE document_id=%s ORDER BY position",
            (document_id,),
        )

    def passages(self, document_id: str) -> list[Row]:
        """A document's passages in order, with what indexing stored for each."""
        return self.db.rows(
            "SELECT id, position, heading, text, context, char_start, char_end, page, "
            "embedding_model FROM chunks WHERE document_id=%s ORDER BY position",
            (document_id,),
        )

    def all_texts(self, exclude_labels: list[str]) -> list[Row]:
        """Every chunk's text with its document name, in document order."""
        return self.db.rows(
            "SELECT c.id, c.text, d.name AS document_name FROM chunks c "
            "JOIN documents d ON d.id=c.document_id WHERE NOT (d.labels && %s::text[]) "
            "ORDER BY d.name, c.position",
            (exclude_labels,),
        )

    def ids(self, document_id: str) -> list[str]:
        return [r["id"] for r in self.for_document(document_id)]

    def texts(self, document_id: str) -> list[str]:
        return [r["text"] for r in self.for_document(document_id)]

    def count(self) -> int:
        row = self.db.one("SELECT count(*) AS n FROM chunks")
        return int(row["n"]) if row else 0

    def embedding_models(self) -> dict[str, int]:
        rows = self.db.rows("SELECT embedding_model, count(*) AS n FROM chunks GROUP BY 1")
        return {r["embedding_model"]: int(r["n"]) for r in rows}

    # -- retrieval ---------------------------------------------------------------------------

    def dense(
        self, vector: list[float], k: int, exclude_labels: list[str]
    ) -> list[tuple[str, float]]:
        """Nearest chunks by cosine similarity (HNSW index)."""
        rows = self.db.rows(
            "SELECT c.id, 1 - (c.embedding <=> %(v)s::vector) AS score "
            "FROM chunks c JOIN documents d ON d.id=c.document_id "
            "WHERE c.embedding IS NOT NULL AND NOT (d.labels && %(ex)s::text[]) "
            "ORDER BY c.embedding <=> %(v)s::vector LIMIT %(k)s",
            {"v": vector_literal(vector), "ex": exclude_labels, "k": k},
        )
        # hnsw.iterative_scan=relaxed_order may return neighbours slightly out of order
        return sorted(((r["id"], float(r["score"])) for r in rows), key=lambda t: (-t[1], t[0]))

    def sparse(
        self, terms: list[str], k: int, exclude_labels: list[str]
    ) -> list[tuple[str, float]]:
        """Chunks matching ANY query term, ranked by cover density (GIN full-text index)."""
        words = [t for t in terms if is_word(t)]
        if not words:
            return []
        rows = self.db.rows(
            "SELECT c.id, ts_rank_cd(c.fts, q.query, 32) AS score "
            "FROM chunks c JOIN documents d ON d.id=c.document_id, "
            "websearch_to_tsquery('english', %(q)s) AS q(query) "
            "WHERE c.fts @@ q.query AND NOT (d.labels && %(ex)s::text[]) "
            "ORDER BY score DESC, c.id LIMIT %(k)s",
            {"q": " or ".join(words), "ex": exclude_labels, "k": k},
        )
        return [(r["id"], float(r["score"])) for r in rows]

    def doc_freq(self, terms: list[str]) -> dict[str, int]:
        """Number of chunks each term occurs in (for IDF weighting)."""
        words = [t for t in terms if is_word(t)]
        if not words:
            return {}
        rows = self.db.rows(
            "SELECT t.term, (SELECT count(*) FROM chunks c "
            "WHERE c.fts @@ plainto_tsquery('english', t.term)) AS df "
            "FROM unnest(%s::text[]) AS t(term)",
            (words,),
        )
        return {r["term"]: int(r["df"]) for r in rows}

    def refresh_lexicon(self) -> None:
        """Rebuild the vocabulary used for spelling correction (call after chunks change)."""
        with self.db.transaction():
            self.db.run("DELETE FROM lexicon")
            self.db.run(
                "INSERT INTO lexicon(word, ndoc) SELECT word, ndoc::int FROM ts_stat("
                "'SELECT to_tsvector(''simple'', text) FROM chunks') WHERE char_length(word) >= 3"
            )

    def known_words(self, words: list[str]) -> set[str]:
        """Words that occur in the corpus, directly or as an inflection (shared stem)."""
        if not words:
            return set()
        rows = self.db.rows(
            "SELECT t.w AS word FROM unnest(%s::text[]) AS t(w) WHERE EXISTS "
            "(SELECT 1 FROM lexicon l WHERE l.word = t.w) OR EXISTS "
            "(SELECT 1 FROM chunks c WHERE c.fts @@ plainto_tsquery('english', t.w))",
            (words,),
        )
        return {r["word"] for r in rows}

    def spelling(self, words: list[str]) -> dict[str, str]:
        """Corrections for words that do not occur in the corpus: word -> closest corpus word.

        Candidates come from the trigram index plus every swap of two adjacent letters (trigram
        similarity misses short transposed words such as "reprot"); the winner has the smallest
        edit distance (typo-sized only), then the most documents. Words that exist are never
        changed.
        """
        words = [w for w in dict.fromkeys(words) if len(w) >= 4]
        known = self.known_words(words)
        fixes: dict[str, str] = {}
        swaps = {
            w: {w[:i] + w[i + 1] + w[i] + w[i + 2 :] for i in range(len(w) - 1)} for w in words
        }
        swapped = self.db.rows(
            "SELECT word, ndoc FROM lexicon WHERE word = ANY(%s)",
            (sorted({v for w in words if w not in known for v in swaps[w]}),),
        )
        for w in words:
            if w in known:
                continue
            rows = [r for r in swapped if r["word"] in swaps[w]] + self.db.rows(
                "SELECT word, ndoc, similarity(word, %(w)s) AS sim FROM lexicon "
                "WHERE word %% %(w)s ORDER BY sim DESC, ndoc DESC LIMIT 12",
                {"w": w},
            )
            allowed = 1 if len(w) <= 8 else 2  # "rewards" is a word, not a typo of "records"
            scored = [(edit_distance(w, r["word"]), -r["ndoc"], r["word"]) for r in rows]
            scored = [t for t in scored if t[0] <= allowed]
            if scored:
                fixes[w] = min(scored)[2]
        return fixes

    def vectors(self, ids: list[str]) -> dict[str, list[float]]:
        rows = self.db.rows(
            "SELECT id, embedding::real[] AS embedding FROM chunks "
            "WHERE id = ANY(%s) AND embedding IS NOT NULL",
            (ids,),
        )
        return {r["id"]: [float(x) for x in r["embedding"]] for r in rows}

    def get_many(self, ids: list[str]) -> list[Row]:
        """Chunks (in the order of ids) with their document metadata."""
        rows = self.db.rows(
            "SELECT c.id, c.document_id, d.name AS document_name, d.labels, c.position, c.text, "
            "c.char_start, c.char_end, c.heading, c.context, c.page "
            "FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id = ANY(%s)",
            (ids,),
        )
        by_id = {r["id"]: r for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def adjacent(self, ids: list[str], offset: int) -> list[Row]:
        """The chunk `offset` positions after (negative: before) each given chunk of the same
        document, with get_many's columns plus `from_id`, the chunk it neighbours."""
        return self.db.rows(
            "SELECT n.id, n.document_id, d.name AS document_name, d.labels, n.position, n.text, "
            "n.char_start, n.char_end, n.heading, n.context, n.page, c.id AS from_id "
            "FROM chunks c JOIN chunks n ON n.document_id=c.document_id "
            "AND n.position=c.position+%s JOIN documents d ON d.id=n.document_id "
            "WHERE c.id = ANY(%s)",
            (offset, ids),
        )

    def refs(self, ids: list[str]) -> dict[str, Row]:
        """Document, page and heading of chunks without their text, keyed by chunk id."""
        rows = self.db.rows(
            "SELECT c.id AS chunk_id, c.document_id, d.name AS document_name, c.page, c.heading "
            "FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id = ANY(%s)",
            (ids,),
        )
        return {r["chunk_id"]: r for r in rows}

    # -- citations ---------------------------------------------------------------------------

    def source_urls_for(self, document_ids: list[str]) -> dict[str, list[str]]:
        rows = self.db.rows(
            "SELECT document_id, array_agg(text ORDER BY position) AS texts FROM chunks "
            "WHERE document_id = ANY(%s) GROUP BY document_id",
            (document_ids,),
        )
        return {r["document_id"]: source_urls(list(r["texts"])) for r in rows}

    def citations(self, items: list[tuple[str, str | None]]) -> list[Row]:
        """Citation objects for (chunk_id, quote) pairs, in order; raises if a chunk is missing."""
        rows = self.get_many([cid for cid, _ in items])
        found = {r["id"]: r for r in rows}
        urls = self.source_urls_for(sorted({r["document_id"] for r in rows}))
        out: list[Row] = []
        for chunk_id, quote in items:
            r = found.get(chunk_id)
            if r is None:
                raise AppError(404, "Source not found")
            out.append(self._citation(r, quote, urls))
        return out

    def citation(self, chunk_id: str, quote: str | None = None) -> Row:
        return self.citations([(chunk_id, quote)])[0]

    @staticmethod
    def _citation(r: Row, quote: str | None, urls: dict[str, list[str]]) -> dict[str, Any]:
        return {
            "chunk_id": r["id"],
            "document_id": r["document_id"],
            "document_name": r["document_name"],
            "text": r["text"],
            "quote": quote,
            "page": r["page"],
            "heading": r["heading"],
            "source_urls": urls.get(r["document_id"], []),
        }
