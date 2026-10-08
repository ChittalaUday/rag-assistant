# Engine evaluation

Each stage is measured on its own, as the architecture recommends, and compared with the previous
implementation (fixed 650-character chunks, Python cosine + keyword overlap over every chunk, open-ended
entity types and free-text relation labels, one monolithic prompt). Numbers come from the real local
models (`qwen3.5:9b`, `all-minilm:l6-v2`) on 2026-10-08/09 with the shipped configuration; every table can
be regenerated with the commands shown. They are smoke metrics on small corpora, not a claim of semantic
correctness.

## Method

| Item | Detail |
|---|---|
| Retrieval corpus | 17 documents: `fixtures/documents` (6 public notes), `fixtures/testing` (5 fictional scenarios), `fixtures/archive/northstar` (6, a second fictional company used as distractors) |
| Retrieval questions | 63 answerable questions from the three `questions.json` files (single, cross-document, follow-up, conflict) with document-level gold and expected keywords |
| "More files" check | The same comparison on 20 documents including three user-supplied files (a policy PDF, an appraisal form, a survey) and 16 questions about them, 5 with misspellings |
| Multi-hop check | `fixtures/multihop-questions.json`: 14 questions whose answer needs two linked facts (a team's lead and whom that person reports to), over an 11-document index (the 6 Northstar documents and the 5 fictional scenarios) |
| Retrieval metrics | `doc_recall` (gold documents among the top k), `kw_coverage` / `kw_full` (share of expected keywords, and all of them, present in the retrieved text), `hit`, `mrr` |
| Extraction gold | `fixtures/gold/relations.json`: 81 hand-labelled core facts over 8 documents. `pair recall` = both endpoints found in one extracted relation; `triple recall` also needs an accepted predicate. Gold is not exhaustive, so precision against it is only a lower bound |
| End to end | `scripts/evaluate.py`: 42 questions (`questions.json` + `testing-questions.json`) against a running API. Pass = every expected keyword in the answer and every expected source **cited** and no invalid evidence |

Evaluations that index documents run in a scratch schema of `knowledge_engine_test`; evaluations on a
populated index (`--live-db`) only read a scratch database built from the same documents. Neither touches
the application database.

## Extraction

`make eval-extraction` (new pipeline) and `python -m knowledge_engine.evaluation graph --api URL` (any server).

| | Old pipeline | New pipeline |
|---|---|---|
| Gold pair recall | 0.519 (42/81) | **0.815** (66/81) |
| Gold triple recall (correct canonical predicate) | 0.000 (labels were free text) | **0.765** (62/81) |
| Entity types over 17 documents | 42 (e.g. `process`, `processes`, `literal source concept`) | 11 (ontology) |
| Relation labels / predicates | 149 distinct, many whole sentences | 17 canonical |
| Entities connected to at least one relation | 64% | 95% |
| Nodes for the survey questionnaire in the user's corpus | 163 (noise) | 0 (no stated facts; text still searchable) |
| Duplicate entities, self-loops | present | none (deterministic identity, validator) |

What moved the needle, in order: ontology-constrained structured output; asking for relations before
entities (entities-first lost abstract objects such as "leave request"); a second worked example for
bullet lists; higher item caps with a larger output budget (dense passages were silently truncated at
20 entities); deterministic validation with evidence expansion within a line; entity resolution that
merges people and organisations but never products. Remaining misses are mostly gold strictness
(the model attributes `responsible_for` to the person who leads a team) and facts stated without a
grammatical subject.

## Retrieval

`make eval-retrieval`. k = 6 passages unless noted.

### 17-document fixture corpus, 63 questions

| System | doc recall | keyword coverage | full coverage | hit | MRR |
|---|---|---|---|---|---|
| legacy (old algorithm) | 0.921 | 0.967 | 0.921 | 0.968 | 0.818 |
| dense only (pgvector) | 0.862 | 0.919 | 0.873 | 0.937 | 0.810 |
| sparse only (full-text) | 0.833 | 0.897 | 0.857 | 0.921 | 0.821 |
| hybrid (rank fusion) | 0.889 | 0.952 | 0.905 | 0.984 | 0.865 |
| **full retrieval** (rewrite + spelling + rerank) | **0.944** | **0.984** | **0.952** | **1.000** | **0.874** |

At k = 8 (the depth used for relationship questions): doc recall 0.979, keyword coverage 0.984, full
coverage 0.952, hit 1.000, MRR 0.874 against 0.955 / 0.967 / 0.921 / 0.968 / 0.818 for the old algorithm.

By question type (k = 8), old algorithm → full retrieval:

| Type | doc recall | full coverage | MRR |
|---|---|---|---|
| single document | 1.000 → 1.000 | 1.000 → 1.000 | 0.812 → **0.893** |
| cross-document | 0.926 → 0.926 | 0.944 → 0.944 | 0.907 → 0.917 |
| conflicting sources | 1.000 → 1.000 | 0.667 → 0.667 | 0.833 → 0.833 |
| follow-up ("she", "that policy") | 0.864 → **1.000** | 0.818 → **1.000** | 0.677 → **0.780** |

The gain is concentrated in follow-ups (one model call rewrites the question into a standalone one; hit
0.82 → 1.00) and in rank quality for single-document questions. Cross-document recall is only on par with
the old algorithm, and conflict questions are limited by their expected keywords, which the retrieved
passages do not all contain; neither improved.

### 20 documents including the user's files, 16 questions (5 misspelt)

| System | k | doc recall | full coverage | hit | MRR |
|---|---|---|---|---|---|
| legacy | 8 | 1.000 | 0.875 | 0.938 | 0.713 |
| dense only | 8 | 1.000 | 0.938 | 0.938 | 0.812 |
| sparse only | 8 | 1.000 | 0.938 | 0.938 | 0.666 |
| hybrid | 8 | 1.000 | 0.938 | 0.938 | 0.736 |
| **full retrieval** | 8 | **1.000** | **1.000** | **1.000** | **0.830** |

Misspelt questions first scored *worse* than the old algorithm (hit 0.60): full-text search cannot match
"apprisal", and the dense query alone was too garbled. Correcting unknown query words against the corpus
vocabulary (trigram index + edit distance, stems treated as known) restored them (hit 1.00).

### Diversity: a penalty for similar passages was measured and removed

The first selection step was maximal marginal relevance (score minus λ × similarity to the passages
already chosen). Tracing an end-to-end failure showed a document ranked fifth being dropped in favour of
dissimilar passages ranked tenth and twelfth, so λ was ablated on the populated 14-document index
(37 questions, graph signals off, k = 8):

| λ | doc recall | cross-document doc recall | full coverage | MRR |
|---|---|---|---|---|
| 0.3 (was) | 0.932 | 0.783 | 0.973 | 0.899 |
| 0.15 | 0.955 | 0.867 | 0.973 | 0.905 |
| 0.05 | 0.964 | 0.867 | 0.973 | 0.905 |
| **0 (shipped)** | **0.973** | **0.900** | 0.973 | 0.901 |

The penalty only pushed second documents out of the top k; no setting improved anything it was meant to
protect. Selection is now score order with near-duplicates (cosine ≥ 0.97, e.g. repeated form rows)
skipped. This is a measured decision for indexes of this size (45–75 passages); a much larger, more
redundant corpus may justify a mild penalty again, and `retrieval.fusion.select` is the one place to add it.

### Does the graph help retrieval?

Tested as recommended: plain hybrid first, then graph signals on the same populated index (14 documents,
37 questions, k = 8).

| Variant | doc recall | keyword coverage | full coverage | hit | MRR |
|---|---|---|---|---|---|
| no graph signals | 0.973 | 0.986 | 0.973 | 1.000 | 0.901 |
| graph list only | 0.910 | 0.986 | 0.973 | 1.000 | 0.864 |
| entity-coverage feature only | 0.950 | 0.986 | 0.973 | 1.000 | 0.874 |
| both, on for every question | 0.815 | 0.896 | 0.865 | 0.919 | 0.755 |

On lookup-style questions graph signals cost accuracy: a well-connected entity (often the corpus's own
subject) pulls in every document that mentions it. Graph retrieval is therefore gated to
relationship-type questions (`KE_GRAPH_RETRIEVAL=relationship`), restricted to relations whose predicate
or other endpoint relates to the question, and limited to reinforcing passages that text search also
found. The graph still feeds the answer prompt (facts with their citations), conflict detection,
highlights and the entity API.

### Multi-hop questions

14 questions over the 11-document multi-hop index (k = 8; `fixtures/multihop-questions.json`):

| System | doc recall | keyword coverage | full coverage | MRR |
|---|---|---|---|---|
| dense only | 0.929 | 0.917 | 0.857 | 0.701 |
| sparse only | 1.000 | 1.000 | 1.000 | 0.871 |
| hybrid (rank fusion) | 0.929 | 0.964 | 0.929 | 0.798 |
| full retrieval, graph signals off | 0.964 | 0.917 | 0.857 | 0.810 |
| **full retrieval, graph signals on** | **1.000** | **1.000** | **1.000** | **0.871** |

Plain retrieval misses two of the 14: "who signed off on the overrun, and what is that person's title" and
"who does the head of People report to, and what is that person's role". In both, the second fact lives
in a passage that shares no words with the question; only the entity (the person) links it. Graph
retrieval reaches both. By detected intent, 11 of the 14 were classified `relationship`, where graph
signals take full coverage from 0.909 to 1.000 (MRR 0.864 → 0.882); the three `lookup` ones are not
graphed by default and plain retrieval gets 0.667 of them.

On the single-hop sets the picture is the opposite (k = 8):

| Detected intent | graph signals | full coverage | MRR |
|---|---|---|---|
| lookup (16) | off | 1.000 | 0.969 |
| lookup (16) | on | 0.875 | 0.622 |
| relationship (14) | off | 0.929 | 0.893 |
| relationship (14) | on | 0.857 | 0.929 |

So the default stays `relationship`: graph retrieval is worth its cost where the answer needs a second
hop, harmful on lookups, and mixed on single-hop relationship questions. Caveats: 14 questions on 44
passages (k = 8 retrieves a fifth of the index), so differences of one question are 0.07; sparse-only
matching the graph variant says these questions are name-heavy, and also that the equal-weight fusion
with a 22 MB embedding model does not always beat full-text search alone.

## Tables, lists and coverage on policy documents

Found by the owner on their own documents and then measured.

**A table cut across chunks reached the model as half a table.** The ratings table of a policy PDF was
split in two chunks; "what are appraisal rewards in percentages?" retrieved only the first (0% and up to
2%) and the answer claimed that nothing was listed for higher scores. The second chunk shares no words with
the question, so ranking could never find it. Now, when a selected passage ends or begins inside a table or
list, the neighbouring chunk (up to two each way, at most four per question) comes along and sits next to
what it completes. The same question now returns all five bands (0%, up to 2%, 2–5%, 5–7%, 7–10%). A
regression test fails without the change. The same output also showed spelling correction turning "rewards"
into "records" (two edits); corrections are now limited to one edit up to eight letters.

**Answers no longer name documents.** The prompt forbids document and file names and false "not listed"
claims; a code safety net removes any "According to <file>" lead-in that still appears. In the last
end-to-end run no answer contained a file name or "According to".

**Why the map seemed to miss things.** On the owner's three documents, read from the running app and a
throwaway copy:

| Document | Before | After re-indexing with this change |
|---|---|---|
| Policy PDF (16 chunks) | 26 entities, 18 relations; of 42 relations the model proposed, 25 were rejected | **31 entities, 25 relations**; 18 rejected |
| Appraisal form (7 chunks) | 0 / 0, no explanation | 0 / 0, now says why in the document list |
| Survey questionnaire (12 chunks) | 0 / 0, no explanation | 0 / 0, now says why in the document list |

Of the 25 rejections, 11 had an endpoint the model never listed as an entity, 11 had evidence that did not
contain both endpoints, and 3 broke type rules. Most of the second group were true statements whose topic
appears only in the section heading above the sentence ("the Manager is required to classify…" under
"4. Performance ratings"), so a heading above the sentence now counts as evidence for an endpoint (still
a verbatim span of the document). Cached model output was re-validated, no new model calls: gold-set recall
is unchanged (pair 0.815, triple 0.765). What remains: forms and questionnaires state few facts, so the
model proposes none, and the rows of a rating table are text, not named entities; both are searchable and
quotable but not on the map. Capturing them would need new entity types and predicates in the ontology
(and a full re-extraction); that is a decision for the owner.

**Live preview.** While a question is typed, the UI asks `POST /api/preview` (debounced, no embedding and
no generation) which entities the text names and which relations bear on it, selects them on the map and
lists them above the input. The same linker runs when the question is sent, so the preview shows what will
drive retrieval. One request per typing burst in a browser check.

## Payloads and scale

Measuring graph payloads on the populated indexes found two problems that grow with the corpus.

| | Before | After |
|---|---|---|
| Graph JSON per edge | ≈ 1.3–1.4 KB (evidence alone 956 B: the full passage text per citation) | **566 B** (a reference to the passage and the verbatim quote) |
| `GET /graph` work | loaded and scanned the full text of every document with evidence, to attach source URLs to each citation | ids, names and quotes only; a test fails if it reads document text |
| `POST /chat` response | the whole graph | only the relations the answer rests on |
| Stored per answer | a full graph snapshot (≈ 150 KB at 104 edges, growing with the corpus) | two lists of highlighted ids |

The UI polls `/graph` every four seconds and never read the embedded passage text (it fetches the source
by `chunk_id`), so both costs were pure overhead. Build time on 100–200 relations is a few milliseconds
either way; the saving is structural, not visible at this size. Stored snapshots from earlier versions
still load (and are returned as highlights only); they are not rewritten.

## End to end

`scripts/evaluate.py`, 42 questions, same 14-document corpus for old and new.

| | Old | New (shipped configuration) |
|---|---|---|
| Passed | 24 / 42 (57%) | **29 / 42 (69%)** |
| Keyword coverage | 0.905 | **0.930** |
| Expected-source coverage | 0.813 | **0.901** |
| Answers containing "homepage" | 15 | **4** |
| Invalid evidence / request errors | 0 / 0 | 0 / 0 |
| Median latency per answer | 3.5 s | 5.4 s (longer answers; an extra model call for follow-ups) |

By set: `questions.json` 16/30 → 19/30; `testing-questions.json` 8/12 → 10/12.

The stack was measured four times: with the diversity penalty 30 / 42, without it 28 / 42, after the
independent review's fixes 27 / 42, and with the table/list, spelling and no-document-name changes
29 / 42 (the table above). Differences of two or three are within run-to-run variation at this size; the
stable result is 24 → 27–30. The route there was not straight, which is why each step was measured:

1. First run with the new pipeline: **20 / 42**, a regression. The retrieval had improved, but the new
   prompt (no worked example, "be concise") produced bare names ("Mallikarjun Kukunuri") instead of
   attributed sentences, so keyword coverage fell.
2. Prompt rewritten to require complete sentences that name things, keep the sources' wording and
   attribute to document names. Style recovered; with graph signals applied to every question the
   CEO question still lost its source (the corpus's own subject entity pulled in every document that
   mentions it).
3. Graph retrieval gated to relationship-type questions: **30 / 42**.
4. Diversity penalty removed after the ablation above: **28 / 42** on this run, with better retrieval
   recall on cross-document questions. After the review fixes (excluded labels no longer leak through
   relations, no inflated weight for words absent from the corpus, and others): **27 / 42**.

Remaining failures (13 in the last run). The classification below was made on an earlier run (15 failures)
by re-running each question and checking the retrieved passages:

| Cause | Questions | Note |
|---|---|---|
| Second expected source retrieved but not cited | 6 | Usually a correct, complete answer from one document that a second document merely corroborates (the legal name in two notes); once the model used a second document's statement without citing it |
| Second expected document never retrieved | 3 | The leadership passage ranks 20th–26th for "who are the leaders" with `all-minilm` because the 22 MB embedding model does not connect "leaders" with "Founder, CEO, CSO" |
| Expected keyword missing from an otherwise sourced answer | 4 | Paraphrase or omission |
| Abstention expected, model answered | 2 | |

Because passing requires citing every expected source, the metric understates answers that are right but
cite fewer corroborating documents; it does not understate the three retrieval misses.

## Findings worth keeping

- **Prompt examples shape answer style.** Dropping the old worked example and asking for "concise"
  answers produced bare names ("Mallikarjun Kukunuri") and pronouns; the smoke metric fell from 24/42 to
  20/42 until the instruction required complete, attributed sentences in the sources' wording.
- **A hard-coded example leaked into answers.** 15 of 42 old answers said "According to its homepage,
  Niruthi …", including for an unrelated HR manual; the generic prompt attributes to document names.
- **More diversity is not more recall.** An MMR penalty reduced cross-document recall (0.900 → 0.783);
  near-duplicate removal is enough at this size.
- **Graph signals are conditional.** They fix questions whose second fact shares no words with the
  question and hurt plain lookups (MRR 0.969 → 0.622), so they are gated by detected intent.
- **Measure payloads, not just accuracy.** The graph endpoint scanned every document's text on each
  poll, invisible at 14 documents.
- **Quality gates must not punish short documents.** A first version skipped extraction for anything under
  12 words, so a one-sentence note would never have produced a relation.
- **Test isolation depends on the database being empty.** A scratch schema falls back to `public`; where
  `public` already holds application tables the harness silently used them. The harness now refuses such
  databases and uses `knowledge_engine_test`.

## Limitations

Small corpora; gold relations and question gold are partial; keyword coverage rewards source wording and
cannot judge paraphrases; one local model plays extractor and answerer; extraction recall on dense prose
is bounded by a ~9B model; latency is higher than before (about 5 s per answer, extraction about
15–20 s per passage). A stronger embedding
model is the most likely retrieval upgrade; the current one (22 MB) is the weak link in the never-retrieved
cases above. Answer citations are chosen by the generating model; a verification step
that checks each statement against its passage is the next generation-side improvement.
