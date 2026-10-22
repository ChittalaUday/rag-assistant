# Graph assisted retrieval implementation plan

## Goal

Improve multi-hop and relationship question retrieval by using a graph of entities and relations
that are explicitly supported by indexed passages. Keep the existing hybrid passage search and the
passage-grounded answer/citation contract. A graph edge is a retrieval hint; its source passage is
the evidence shown to the answer model and cited to the user.

## Why this needs an experiment first

The previous graph implementation did not improve answers on the 124-question evaluation set
(0.758 correct with and without extraction) and added 15–20 seconds of extraction per passage.
The LightRAG smoke probe also produced an inferred relation whose description was not in the
source. Therefore this plan does not adopt LightRAG or restore graph retrieval wholesale. It starts
with a small, measured, evidence-linked prototype and has a stop gate before production integration.

## Target flow

```text
document -> parse/chunk -> hybrid passage index
                     -> bounded entity/relation extraction -> validation -> graph index
question -> hybrid passage retrieval ----------------------┐
         -> entity linking -> bounded graph traversal -----┴-> evidence passages -> answer/citations
```

The passage index remains authoritative. Graph data is derived and rebuildable. Extraction output
is untrusted model data and may be stored only after structural and quote validation.

## Stages

### 0. Baseline and test set

- Preserve the current `make eval-retrieval` and `make eval-answers` results/configuration as the
  comparison baseline before retrieval changes.
- Create a focused graph question slice from existing evaluation fixtures: direct relations,
  two-hop relations, ambiguous names, unsupported relations, cross-document links, and unanswerable
  questions. Record expected source passage/document IDs, not just answer keywords.
- Include extraction cost, graph build time, added passages, expected-source recall, answer accuracy,
  citation correctness, and unsupported-claim rate in the comparison.

**Gate:** the test slice must be reproducible and use only `knowledge_engine_test` scratch schemas.

### 1. Evidence-linked extraction prototype (no production graph writes)

- Add a bounded extraction function using the existing local model gateway and a strict Pydantic
  schema: entity surface form, optional type, relation subject/object/label, and exact evidence
  quote. Do not add a fixed list of business/entity types or relation labels.
- Validate each quote against the exact normalized passage; require the quote to contain or
  unambiguously refer to both endpoints. Reject invalid JSON and unsupported items conservatively.
- Exercise it on a small representative fixture corpus and report precision, recall, latency, and
  tokens. Keep raw model outputs only in ignored local evaluation artifacts, never in fixtures or
  tracked runtime data.

**Gate:** stop if extraction cost or relation precision makes graph expansion worse than existing
second-hop retrieval. No production behavior changes in this stage.

### 2. Persistent graph index

- Add an Alembic migration and repositories under `store/` for canonical entities, aliases,
  relations, and per-passage evidence. Keep all SQL inside `store/`.
- Keep evidence rows per relation and source passage; never merge away provenance. Normalize lookup
  keys in code, but do not merge names by model assertion alone. Preserve ambiguous candidates.
- Write graph rows in the same publication transaction as replacement chunks. Failed replacement
  leaves the previously published chunks and graph intact. Deletion removes graph rows through
  document/passage ownership constraints; reindex/version changes rebuild them.
- Cache extraction results by passage content and extractor/prompt version, using the existing
  model cache only if its value shape and invalidation rules fit cleanly.

**Gate:** tests cover hashing/deduplication, replacement rollback, deletion, cache invalidation,
quote validation, and ambiguous entity identity.

### 3. Graph-assisted retrieval

- Resolve question names against exact entity/alias keys; allow fuzzy matching only as a ranked
  candidate, never as silent identity merging.
- Traverse a bounded number of edges and hops, rank by question/entity match and evidence quality,
  then fetch the supporting passages. Apply the existing evidence budget and duplicate removal.
- Combine graph-derived passages with dense, sparse, reranked, structural-neighbour, and current
  second-hop results. Graph results are append-only initially so they cannot displace direct hits.
- Add trace fields showing linked entities, traversed relation/evidence IDs, hop count, and why each
  passage was selected. The answer model still sees the source passages, not unsupported graph
  summaries.

**Gate:** graph mode must improve the focused multi-hop set without lowering overall answer accuracy,
citation validity, or abstention quality. Compare latency and extraction/indexing cost as well.

### 4. API and UI exposure

- Only after retrieval is useful, extend trace/API schemas with graph retrieval details. Preserve
  existing response compatibility where possible; document any OpenAPI changes.
- Reuse the existing graph UI for browsing if it helps users inspect evidence, but distinguish
  extracted, quote-validated relations from the current capitalization-derived preview graph.
- Do not expose unvalidated model output as a fact or relation.

### 5. Rollout and maintenance

- Keep graph retrieval behind a setting until full retrieval and answer evaluations pass.
- Re-run `make eval-retrieval` and `make eval-answers` before and after each pipeline change and
  retain the feature only when the gain is measurable and repeatable.
- Document model/runtime cost, supported traversal depth, ambiguity behavior, rebuild behavior,
  and known language limitations in `docs/ARCHITECTURE.md` and a dated evaluation report.

## Initial implementation boundary

The first code change should be Stage 0/1 only: a small evaluation slice and isolated extraction
validation. Do not restore graph tables, change production retrieval, add a graph database, or add a
new dependency until that experiment passes its gate.
