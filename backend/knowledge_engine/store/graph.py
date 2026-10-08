"""Entity/relation graph storage. Entities are canonical (merged across documents); every relation
is backed by evidence rows pointing at verbatim spans of source chunks."""

from knowledge_engine.store.base import Db, Row

# noisy-OR over independent evidence: more corroborating sources -> higher confidence
_CONFIDENCE = "1 - exp(sum(ln(1 - least(ev.confidence, 0.99))))"


class GraphRepo:
    def __init__(self, db: Db):
        self.db = db

    # -- writes (called inside the publish transaction) ---------------------------------------

    def exact(self, cls: str, keys: list[str]) -> dict[str, list[str]]:
        """name key -> entity ids for same-class entities whose canonical or alias key matches.

        More than one id means the key is ambiguous (two entities share that alias).
        """
        rows = self.db.rows(
            "SELECT key, id FROM entities WHERE class=%s AND key = ANY(%s) UNION "
            "SELECT a.key, e.id FROM entity_aliases a JOIN entities e ON e.id=a.entity_id "
            "WHERE e.class=%s AND a.key = ANY(%s)",
            (cls, keys, cls, keys),
        )
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(r["key"], []).append(r["id"])
        return out

    def similar(self, cls: str, key: str, limit: int = 5) -> list[Row]:
        """Trigram-similar same-class entities (canonical or alias keys), best first."""
        return self.db.rows(
            "SELECT id, key, similarity(key, %(k)s) AS sim FROM entities "
            "WHERE class=%(c)s AND key %% %(k)s UNION ALL "
            "SELECT e.id, a.key, similarity(a.key, %(k)s) FROM entity_aliases a "
            "JOIN entities e ON e.id=a.entity_id WHERE e.class=%(c)s AND a.key %% %(k)s "
            "ORDER BY sim DESC LIMIT %(n)s",
            {"k": key, "c": cls, "n": limit},
        )

    def create_entity(self, id: str, type_: str, cls: str, name: str, key: str) -> None:
        self.db.run(
            "INSERT INTO entities(id,type,class,canonical_name,key) VALUES(%s,%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING",
            (id, type_, cls, name, key),
        )

    def add_aliases(self, entity_id: str, aliases: list[tuple[str, str]]) -> None:
        self.db.many(
            "INSERT INTO entity_aliases(entity_id,alias,key) VALUES(%s,%s,%s) "
            "ON CONFLICT DO NOTHING",
            [(entity_id, alias, key) for alias, key in aliases],
        )

    def add_mentions(self, rows: list[tuple[str, str, int]]) -> None:
        self.db.many(
            "INSERT INTO entity_mentions(entity_id,chunk_id,count) VALUES(%s,%s,%s) "
            "ON CONFLICT (entity_id, chunk_id) DO UPDATE SET count=EXCLUDED.count",
            rows,
        )

    def add_relation(self, id: str, subject_id: str, predicate: str, object_id: str) -> None:
        self.db.run(
            "INSERT INTO relations(id,subject_id,predicate,object_id) VALUES(%s,%s,%s,%s) "
            "ON CONFLICT DO NOTHING",
            (id, subject_id, predicate, object_id),
        )

    def add_evidence(
        self, rows: list[tuple[str, str, str | None, str, int, int, float, str]]
    ) -> None:
        """(relation_id, chunk_id, phrase, quote, quote_start, quote_end, confidence, extractor)"""
        self.db.many(
            "INSERT INTO relation_evidence(relation_id,chunk_id,phrase,quote,quote_start,quote_end,"
            "confidence,extractor) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            rows,
        )

    def gc(self) -> None:
        """Drop relations without evidence and entities nothing mentions or relates."""
        self.db.run(
            "DELETE FROM relations r WHERE NOT EXISTS "
            "(SELECT 1 FROM relation_evidence e WHERE e.relation_id=r.id)"
        )
        self.db.run(
            "DELETE FROM entities e WHERE NOT EXISTS "
            "(SELECT 1 FROM entity_mentions m WHERE m.entity_id=e.id) AND NOT EXISTS "
            "(SELECT 1 FROM relations r WHERE r.subject_id=e.id OR r.object_id=e.id)"
        )

    # -- reads ---------------------------------------------------------------------------------

    def nodes(self, ids: list[str] | None = None) -> list[Row]:
        """Entities with the documents that mention them (only the given ids when set)."""
        where = "WHERE e.id = ANY(%s) " if ids is not None else ""
        return self.db.rows(
            "SELECT e.id, e.canonical_name AS label, e.type, "
            "COALESCE(array_agg(DISTINCT c.document_id) FILTER (WHERE c.document_id IS NOT NULL), "
            "'{}') AS document_ids, COALESCE(sum(m.count), 0)::int AS mentions "
            "FROM entities e LEFT JOIN entity_mentions m ON m.entity_id=e.id "
            f"LEFT JOIN chunks c ON c.id=m.chunk_id {where}"
            "GROUP BY e.id ORDER BY e.canonical_name, e.id",
            (ids,) if ids is not None else (),
        )

    def relations(
        self,
        entity_ids: list[str] | None = None,
        min_confidence: float = 0.0,
        limit: int = 5000,
        relation_ids: list[str] | None = None,
        exclude_labels: list[str] | None = None,
    ) -> list[Row]:
        """Relations with aggregated confidence: all, those touching the given entities, or
        only the given relation ids. Evidence in documents with an excluded label does not count,
        so a relation only those documents support is not returned."""
        clauses = []
        if entity_ids:
            clauses.append("(r.subject_id = ANY(%(ids)s) OR r.object_id = ANY(%(ids)s))")
        if relation_ids is not None:
            clauses.append("r.id = ANY(%(rids)s)")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        return self.db.rows(
            "SELECT r.id, r.subject_id, r.predicate, r.object_id, "
            f"{_CONFIDENCE} AS confidence, count(*)::int AS evidence_count, "
            "s.canonical_name AS subject_name, o.canonical_name AS object_name, "
            "s.type AS subject_type, o.type AS object_type "
            "FROM relations r JOIN relation_evidence ev ON ev.relation_id=r.id "
            "JOIN chunks c ON c.id=ev.chunk_id JOIN documents d ON d.id=c.document_id "
            "AND NOT (d.labels && %(ex)s::text[]) "
            "JOIN entities s ON s.id=r.subject_id JOIN entities o ON o.id=r.object_id "
            f"{where} GROUP BY r.id, s.id, o.id HAVING {_CONFIDENCE} >= %(min)s "
            "ORDER BY confidence DESC, evidence_count DESC, r.id LIMIT %(n)s",
            {
                "ids": entity_ids or [],
                "rids": relation_ids or [],
                "ex": exclude_labels or [],
                "min": min_confidence,
                "n": limit,
            },
        )

    def evidence(
        self,
        relation_ids: list[str],
        per_relation: int = 5,
        exclude_labels: list[str] | None = None,
    ) -> list[Row]:
        return self.db.rows(
            "SELECT * FROM (SELECT ev.relation_id, ev.chunk_id, ev.quote, ev.phrase, "
            "ev.confidence, row_number() OVER (PARTITION BY ev.relation_id "
            "ORDER BY ev.confidence DESC, ev.id) AS rn "
            "FROM relation_evidence ev JOIN chunks c ON c.id=ev.chunk_id "
            "JOIN documents d ON d.id=c.document_id "
            "WHERE ev.relation_id = ANY(%(r)s) AND NOT (d.labels && %(ex)s::text[])) t "
            "WHERE rn <= %(n)s ORDER BY relation_id, rn",
            {"r": relation_ids, "ex": exclude_labels or [], "n": per_relation},
        )

    def extractor_versions(self) -> dict[str, int]:
        """Evidence rows per extractor version string (model | ontology | prompt version)."""
        rows = self.db.rows(
            "SELECT extractor, count(*)::int AS n FROM relation_evidence GROUP BY 1"
        )
        return {r["extractor"]: r["n"] for r in rows}

    def evidence_in_chunks(self, chunk_ids: list[str]) -> list[Row]:
        """Evidence rows (with relation endpoints) that live in the given chunks."""
        return self.db.rows(
            "SELECT ev.relation_id, ev.chunk_id, ev.quote, ev.phrase, ev.confidence, "
            "r.subject_id, r.predicate, r.object_id, s.canonical_name AS subject_name, "
            "o.canonical_name AS object_name "
            "FROM relation_evidence ev JOIN relations r ON r.id=ev.relation_id "
            "JOIN entities s ON s.id=r.subject_id JOIN entities o ON o.id=r.object_id "
            "WHERE ev.chunk_id = ANY(%s) ORDER BY ev.confidence DESC, ev.id",
            (chunk_ids,),
        )

    def link(self, keys: list[str]) -> list[Row]:
        """Entities whose canonical or alias key equals one of the keys."""
        if not keys:
            return []
        degree = (
            "(SELECT count(*) FROM relations r WHERE r.subject_id=e.id OR r.object_id=e.id)::int"
        )
        return self.db.rows(
            f"SELECT e.id, e.canonical_name AS name, e.type, e.class, e.key AS matched, "
            f"{degree} AS degree FROM entities e WHERE e.key = ANY(%(k)s) "
            f"UNION ALL SELECT e.id, e.canonical_name, e.type, e.class, a.key, {degree} "
            "FROM entity_aliases a JOIN entities e ON e.id=a.entity_id WHERE a.key = ANY(%(k)s)",
            {"k": keys},
        )

    def fuzzy_link(self, key: str, threshold: float, limit: int = 3) -> list[Row]:
        """Entities whose key is trigram-similar to a (possibly misspelled) mention."""
        return self.db.rows(
            "SELECT e.id, e.canonical_name AS name, e.type, e.class, e.key AS matched, "
            "similarity(e.key, %(k)s) AS sim FROM entities e WHERE e.key %% %(k)s "
            "AND similarity(e.key, %(k)s) >= %(t)s ORDER BY sim DESC LIMIT %(n)s",
            {"k": key, "t": threshold, "n": limit},
        )

    def token_candidates(self, word: str, limit: int = 20) -> list[Row]:
        """Entities with a name or alias holding a word similar to `word` (trigram word similarity,
        so a misspelt first name or one-word name is found), and how many passages mention each."""
        return self.db.rows(
            "SELECT e.id, e.canonical_name AS name, e.type, x.key AS matched, "
            "(SELECT count(DISTINCT m.chunk_id) FROM entity_mentions m "
            "WHERE m.entity_id=e.id)::int AS passages "
            "FROM entities e JOIN (SELECT id AS entity_id, key FROM entities UNION "
            "SELECT entity_id, key FROM entity_aliases) x ON x.entity_id=e.id "
            "WHERE %(w)s <%% x.key LIMIT %(n)s",
            {"w": word, "n": limit},
        )

    def search(self, query: str, type_: str | None, limit: int, offset: int = 0) -> list[Row]:
        pattern = "%" + query.strip().replace("%", "").replace("_", " ") + "%"
        return self.db.rows(
            "SELECT e.id, e.canonical_name AS name, e.type, "
            "(SELECT count(*) FROM relations r WHERE r.subject_id=e.id OR r.object_id=e.id)::int "
            "AS degree, (SELECT coalesce(sum(m.count), 0) FROM entity_mentions m "
            "WHERE m.entity_id=e.id)::int AS mentions FROM entities e "
            "WHERE (%(q)s = '%%%%' OR e.canonical_name ILIKE %(q)s OR EXISTS (SELECT 1 FROM "
            "entity_aliases a WHERE a.entity_id=e.id AND a.alias ILIKE %(q)s)) "
            "AND (%(t)s::text IS NULL OR e.type = %(t)s) "
            "ORDER BY degree DESC, mentions DESC, e.canonical_name LIMIT %(n)s OFFSET %(o)s",
            {"q": pattern, "t": type_, "n": limit, "o": offset},
        )

    def entity(self, id: str) -> Row | None:
        row = self.db.one(
            "SELECT id, canonical_name AS name, type, class FROM entities WHERE id=%s", (id,)
        )
        if row is None:
            return None
        row["aliases"] = [
            r["alias"]
            for r in self.db.rows(
                "SELECT alias FROM entity_aliases WHERE entity_id=%s ORDER BY alias", (id,)
            )
        ]
        row["documents"] = self.db.rows(
            "SELECT d.id, d.name, sum(m.count)::int AS mentions FROM entity_mentions m "
            "JOIN chunks c ON c.id=m.chunk_id JOIN documents d ON d.id=c.document_id "
            "WHERE m.entity_id=%s GROUP BY d.id ORDER BY mentions DESC",
            (id,),
        )
        return row

    def mention_chunks(self, entity_ids: list[str], limit: int) -> list[tuple[str, float]]:
        """Chunks mentioning the entities, most mentions first."""
        rows = self.db.rows(
            "SELECT chunk_id, sum(count)::float AS score, count(*) AS n FROM entity_mentions "
            "WHERE entity_id = ANY(%s) GROUP BY chunk_id "
            "ORDER BY n DESC, score DESC, chunk_id LIMIT %s",
            (entity_ids, limit),
        )
        return [(r["chunk_id"], float(r["score"])) for r in rows]

    def mentions_in(self, chunk_ids: list[str]) -> dict[str, set[str]]:
        rows = self.db.rows(
            "SELECT chunk_id, entity_id FROM entity_mentions WHERE chunk_id = ANY(%s)", (chunk_ids,)
        )
        out: dict[str, set[str]] = {}
        for r in rows:
            out.setdefault(r["chunk_id"], set()).add(r["entity_id"])
        return out

    def stats(self) -> Row:
        return (
            self.db.one(
                "SELECT (SELECT count(*) FROM entities) AS entities, "
                "(SELECT count(*) FROM relations) AS relations, "
                "(SELECT count(*) FROM relation_evidence) AS evidence"
            )
            or {}
        )

    def degrees(self, entity_ids: list[str]) -> dict[str, int]:
        rows = self.db.rows(
            "SELECT e.id, (SELECT count(*) FROM relations r WHERE r.subject_id=e.id "
            "OR r.object_id=e.id)::int AS degree FROM entities e WHERE e.id = ANY(%s)",
            (entity_ids,),
        )
        return {r["id"]: int(r["degree"]) for r in rows}
