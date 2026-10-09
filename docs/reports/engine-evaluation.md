# Engine evaluation

Each stage is measured on its own and end to end. The first section is the current state (2026-10-09).
The sections after it measured the graph-based engine against the original implementation (fixed 650-character chunks, Python cosine + keyword overlap over every chunk, open-ended
entity types and free-text relation labels, one monolithic prompt). Numbers come from the real local
models (`qwen3.5:9b`, `all-minilm:l6-v2`) on 2026-10-08/09 with the shipped configuration; every table can
be regenerated with the commands shown. They are smoke metrics on small corpora, not a claim of semantic
correctness.

## 2026-10-09: Indian languages and chunking (UDA-94 phase 1, UDA-100)

### Indian languages

`make eval-multilingual`: 46 questions over the fixture corpus plus 12 fictional documents, one per
language (Hindi, Bengali, Tamil, Telugu, Marathi, Gujarati, Kannada, Malayalam, Odia, Punjabi, Urdu,
Assamese; `fixtures/multilingual/`): 24 asked in the document's language, 12 in English about an
Indian-language document, 3 romanised (Hindi, Bengali, Tamil), 5 in Indian languages about English
documents, 2 unanswerable. Keywords are numbers or native-script names; the scorer treats any script's
digits and digit grouping alike (२१५ = 215, 2,00,000 = 200000).

| Configuration | correct | native | English about Indic | romanised | Indic about English | flagged |
|---|---|---|---|---|---|---|
| before (commit e169724) | 0.761 | 0.625 | 0.917 | 0.667 | 1.000 | 0.652 |
| fixes below, qwen3.5:9b | 0.848 | 0.750 | 1.000 | 0.667 | 1.000 | 0.022 |
| same, gemma4:12b as answer model | **0.978** | **1.000** | 0.917 | **1.000** | 1.000 | 0.022 |

Retrieval already found the right document for every question before the fixes (dense search is
multilingual), but the text layer was broken for Indian scripts: Python's `\w` excludes combining
marks, so every Indic word was cut at its vowel signs and viramas ("हिन्दी" → "ह", "नद") and accent
folding deleted the marks. Keyword search, term coverage, the second hop and the sentence check saw
nothing in those scripts. Fixed with one shared word pattern (letters, digits, every Unicode combining
mark, ZWJ/ZWNJ) and accent folding for Latin letters only; spelling correction leaves non-Latin words
alone (an inflected form is one edit from its stem). The answer prompt asks for the question's
language; the sentence check no longer requires shared words across scripts (that alone flagged 65%
of answers). With qwen3.5:9b six questions about people in Telugu, Gujarati, Kannada, Odia, Punjabi
and Urdu were still answered in English; the prompt now names the question's script and a wrong-script
answer gets one retry (unit-tested; its effect on the real models was not measured before the owner
stopped the test runs). English is unchanged by these changes: 0.831 correct on the 124 questions
before and after.

**Answer model.** gemma4:12b (8.0 GB) answered 45 of 46 against 37 for qwen3.5:9b (6.6 GB), at about
26 s against 16 s per answer in that run. Its English score was not measured (the run was stopped), so
qwen3.5:9b remains the default and `KE_CHAT_MODEL=gemma4:12b` is the documented choice for
Indian-language use; switching the default waits for the English comparison.

**Embeddings.** embeddinggemma, qwen3-embedding:0.6b and bge-m3 all found the right document for every
question (dense recall 1.000; MRR 0.951–0.977); with one document per language the set is too easy to
separate them, so embeddinggemma (best on English) stays.

### Chunking: structure, hybrid and Chonkie

`python -m knowledge_engine.evaluation chunking`: 65 documents (the 29 fixtures plus 36 Wikipedia
articles about Indian institutions and schemes in English, Hindi, Tamil and Bengali, up to 15,000
characters each, fetched by `scripts/fetch_chunking_corpus.py` into `work/`), 144 questions generated
from adjacent sentence pairs and kept only when both answer keywords occur verbatim in their sentences.
Same embedding model, 700-character limit and hybrid search (no reranker, summaries or second hop) for
every chunker; Chonkie (1.7.0) gets the engine's embedding model and Indian sentence delimiters.

| Chunker | recall@5 | both facts in one retrieved chunk | both facts in one chunk (index) | chunks | avg chars | ingestion |
|---|---|---|---|---|---|---|
| **engine, structure (default)** | **1.000** | **1.000** | **1.000** | 1,014 | 536 | **47 s** |
| engine, hybrid (`KE_CHUNKER=hybrid`) | 1.000 | 1.000 | 1.000 | 1,122 | 494 | 136 s |
| Chonkie RecursiveChunker | 0.986 | 0.833 | 0.840 | 989 | 490 | 46 s |
| Chonkie SemanticChunker | 0.958 | 0.639 | 0.646 | 1,783 | 272 | 213 s |

Semantic chunking was not more accurate: Chonkie's semantic chunker split a third of the linked
sentence pairs and made 75% more, smaller chunks at the highest ingestion cost. The engine's chunkers
keep the last sentence of a chunk at the start of the next (overlap), which is most of why adjacent
facts stay together. Structure and hybrid tie on this set (adjacent pairs are too easy for overlapping
chunkers), so the cheaper structure chunker stays the default and hybrid remains a setting.

Chunker fixes found while building the tests (each reproduced first): the danda and the Urdu full stop
were not sentence ends, so Indian-language text was cut mid-sentence; a tiny tail could be folded into
a chunk beyond the size limit (752 > 700 characters); a short final section was swallowed into the
previous section's chunk under the wrong heading. Passages now also respect a hard token limit: the
embedding model's window (2,048 tokens for embeddinggemma) minus 384 reserved for the context, counted
with the model's own tokenizer for any passage longer than the budget in UTF-8 bytes and split at
sentence ends when over.

## 2026-10-09: accuracy work (UDA-82)

What changed, and what each change was worth, on one bigger test set and one scorer. The sections
after this one describe the earlier, graph-based engine and are kept as the record of how it was
measured; extraction, the graph and `/graph` no longer exist.

### Method

| Item | Detail |
|---|---|
| Corpus | The 17 fixture documents (6 public notes, 5 fictional scenarios, 6 Northstar documents), indexed in a scratch schema of `knowledge_engine_test` |
| Questions | **124**: the 30 + 12 + 30 existing questions, the 14 multi-hop questions, and 38 new hand-written ones (`fixtures/paraphrase-questions.json`): 31 that avoid the documents' wording (incl. 3 list questions) and 7 near-miss questions with no answer in the documents |
| Runner | `python -m knowledge_engine.evaluation answers [--set key=value]` (`make eval-answers`): the real `chat` pipeline in process, follow-ups chained, real local models |
| Scores | `correct`: every expected keyword in the answer and at least one expected document cited (unanswerable: the answer declines). `strict`: also every expected document cited (the old pass rule). Also retrieval recall of the expected documents, latency, and the share of answers with a sentence no passage supports (`flagged`) |
| Scorer fixes | The stale keyword "FICTIONAL TEST SCENARIO" (the old pipeline quoted labels) was dropped and abstention detection widened ("not explicitly named"); every run below is rescored with the same scorer (`rescore`) |

Keyword scoring rewards the documents' wording: "January 2029" for "2029-01" or "deduplication rework"
for "dedupe rework" count as misses. Differences of one or two questions (0.01–0.02) are within
run-to-run variation of the answer model.

### The graph does not help answers

Same questions, same shipped configuration of commit 1f5b910 (all-minilm, graph retrieval gated to
relationship questions), with and without extraction:

| | correct | strict |
|---|---|---|
| graph (extraction on) | 0.758 | 0.694 |
| no graph | 0.758 | **0.726** |

Three questions were right only with the graph and three only without it. Extraction costs 15–20 s
per passage at indexing and doubles the code of the engine, so it was removed (migration 0005 drops
the tables). Two-hop questions are covered by the second retrieval round below.

### Embedding models

Retrieval only (`make eval-retrieval`, 108 answerable questions, k = 8, `work/emb-*.json`). "full" =
every expected keyword among the retrieved passages.

| Model (task prefixes) | size | dense full | dense, paraphrased questions | dense MRR | full retrieval |
|---|---|---|---|---|---|
| all-minilm:l6-v2 (was) | 45 MB, 384-d | 0.843 | 0.786 | 0.685 | 0.898 |
| nomic-embed-text | 274 MB, 768-d | 0.852 | 0.679 | 0.724 | 0.880 |
| qwen3-embedding:0.6b, with instruction | 639 MB, 1024-d | 0.741 | 0.429 | 0.614 | 0.889 |
| qwen3-embedding:0.6b, no prefix | 639 MB, 1024-d | 0.880 | 0.750 | 0.723 | 0.926 |
| mxbai-embed-large | 670 MB, 1024-d | 0.935 | 0.893 | 0.770 | 0.926 |
| bge-m3 | 1.2 GB, 1024-d | 0.935 | 0.857 | 0.803 | 0.917 |
| **embeddinggemma** | 621 MB, 768-d | **0.954** | **0.929** | **0.814** | **0.944** |

embeddinggemma is the new default (it is also multilingual, the starting point for the Indian-language
work). qwen3-embedding:4b was not tested: 2560-d exceeds pgvector's 2,000-d HNSW limit and conflicts with
the low-resource goal.

### Retrieval features (on embeddinggemma)

Each feature alone against the full retrieval without it (k = 8):

| Variant | full | MRR | paraphrased: full / MRR |
|---|---|---|---|
| features only (was) | 0.944 | 0.795 | 0.893 / 0.598 |
| + reranker Qwen3-Reranker-0.6B, weight 0.7 | 0.954 | **0.875** | 0.929 / **0.779** |
| + reranker, weight 1.0 | 0.954 | 0.879 | 0.929 / 0.772 |
| + second hop on every question | 0.963 | 0.807 | 0.964 / 0.607 |
| + second hop on relationship questions only | 0.944 | 0.798 | 0.893 / 0.599 |
| + contextual summaries by qwen3:1.7b | 0.954 | 0.831 | 0.893 / 0.714 |
| **+ contextual summaries by qwen3.5:9b** | **0.972** | 0.818 | **1.000** / 0.750 |

- **Reranker.** Ollama has no rerank endpoint; the engine scores each candidate with one
  `/api/generate` call of a Qwen3-Reranker GGUF (raw prompt, one output token, `logprobs`) and takes
  P(yes)/(P(yes)+P(no)): about 50 ms per passage, ~1.5 s for 30 candidates. It mainly ranks the right
  passage first more often (MRR 0.795 → 0.875). Weight 0.7 keeps the transparent features in the mix
  at the same accuracy as 1.0.
- **Second hop.** Gated to relationship-type questions it never fired usefully (the intent regex rarely
  matches). On every question it adds at most two passages from documents not yet selected and lifts
  recall without touching the ranking. The link words were first chosen by rarity alone and picked
  generic words ("lead", "unavailable"); names (capitalised words) are now preferred.
- **Contextual summaries** ("contextual retrieval"): one model-written sentence or two per passage,
  indexed with it. The 9B model's summaries help clearly more than the 1.7B model's; they cost one model
  call per passage at indexing (the 17-document corpus: 116 s with summaries), cached afterwards.

### End to end

`make eval-answers` with the settings shown, 124 questions, every run rescored with the same scorer
(`work/acc-*.json`). Latency is per answer on an M5 Pro while other evaluations shared the model, so
compare it only roughly.

| Configuration | correct | strict | expected sources cited | expected documents retrieved | s / answer |
|---|---|---|---|---|---|
| graph, all-minilm (commit 1f5b910) | 0.758 | 0.694 | 0.878 | 0.968 | 16.8 |
| no graph, all-minilm | 0.758 | 0.726 | 0.887 | 0.976 | 10.1 |
| embeddinggemma + sentence check | 0.806 | 0.766 | 0.915 | 0.984 | 10.6 |
| + reranker | 0.815 | 0.782 | 0.938 | 0.989 | 12.8 |
| + prompt rule and sentence-check fixes | 0.823 | 0.774 | 0.934 | 0.989 | 9.7 |
| **+ contextual summaries + second hop (shipped)** | **0.831** | **0.790** | **0.953** | **0.996** | 10.6 |

By question type, before and after:

| Type (n) | graph, all-minilm | shipped |
|---|---|---|
| single (28) | 0.821 | 0.893 |
| cross-document (18) | 0.722 | 0.556 |
| follow-up (11) | 0.727 | 0.818 |
| conflict (6) | 0.333 | 0.333 |
| multi-hop (14) | 0.786 | **1.000** |
| paraphrased (28) | 0.821 | **1.000** |
| list (3) | 0.333 | 0.667 |
| unanswerable (16) | 0.812 | 0.812 |

The cross-document drop is answer wording, not retrieval: the expected documents were retrieved for
17 of 18 questions, and 6 of the 8 misses are keyword-only ("Pvt. Ltd" without its full stop,
"approves" for "approval", "Platform rotating as secondary" for "Platform secondary"); one is a real
detail error (the uptime figure given where the passage names the RPO requirement). Indexing the 17
documents takes 1.6 s without and 100 s with contextual summaries (one 9B call per passage, cached).

**Thinking** (`KE_ANSWER_THINKING`) was not adopted: the first answers took 70–110 s each (about ten
times longer) and the run was stopped; it stays available as a setting.

### The sentence check

After the answer, one model call lists for each sentence the passages that state it. Measured
effects:

- **Citations.** Answers cite corroborating passages they used to miss (expected sources cited 0.887 →
  0.953; `strict` rises with it).
- **It finds real errors.** In the combined run 9 of 124 answers had a sentence no passage supports.
  Among them was a hallucinated answer ("Volans Logistics and Bluefin Oil & Gas were onboarded through
  Delta-V", while the passage names Ironhide Mining and Parallax Grid Co.).
- **Its own errors, found in a browser check and fixed.** (1) With the instruction "states it", the 9B
  model refused a sentence combining two facts from two passages; the instruction now accepts each
  passage that states one of the sentence's facts. (2) That made it over-generous (seven passages,
  including boilerplate, for one sentence), so a match is kept only if the passage shares at least two
  content words with the sentence. (3) The splitter cut "Dr." and "Pvt. Ltd." into sentences of their
  own (5 of the 9 flags); abbreviations and initials no longer end a sentence.
- It costs one extra model call per answer (about 1–2 s).

### Remaining failures

Most remaining misses are answers that leave out a qualifier the documents give ("$42M", "Munich",
"retired NASA scientist") or paraphrase an expected keyword; a few answer a nearby question instead of
declining (sick days answered with annual leave, a European region answered with us-west-2, Niruthi's
leave policy answered from the fictional scenario). The answer prompt now tells the model to answer the
question that was asked and to say when the passages only give related information. Conflict questions
(6) stay at 1–2 of 6: their expected keywords ("supersedes", "canonical", "not updated") are the
documents' words for the disagreement and the model rarely repeats them.

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
no generation) which entities the text names and which relations bear on it, and selects them on the map
(no separate list). The same linker runs when the question is sent, so the preview shows what will drive
retrieval. It first fixes misspellings the way a sent question is fixed (leaving the word still being
typed), and links names loosely: a misspelt name ("leela dass", "monsoon lenz"), a first or last name
alone ("Leela"), a misspelt one-word name ("niruti"), and a transposed verb ("reprot to") all select the
right nodes and relations; an everyday word that is also part of a name ("policy" for "Policy Board") does
not. A word links on its own only if exactly one named entity contains it (or one entity is named by
that word alone) and the corpus uses the word almost only inside that name.

## Capacity and load

Measured on one machine (Apple M5 Pro, 15 cores, 24 GB, chat model `qwen3.5:9b` and `all-minilm` through
Ollama, PostgreSQL local), against a throwaway copy of the owner's data, before the graph was removed.
Request mix: real questions from the fixtures. The serialisation, memory and corpus-growth findings still
hold; the entity-linking and `/graph` rows describe code that no longer exists.

**Memory and disk.** The chat model holds 5.8 GB (4.8–8 GB resident seen from the process during load,
the difference being context cache); the embedding model 25 MB; the backend process 56–61 MB under load;
PostgreSQL 34 MB resident for 14 documents. Parsing with Docling peaked at 1.06 GB (a 16-page PDF, 29 s,
first use) and 0.40 GB (a DOCX, 4 s). Install: 1.3 GB Python environment, 0.5 GB Docling models, 6.6 GB chat
model on disk.

**Answers are serialised by the model slot.** The same eight questions at each concurrency level
(`answers per minute` is constant, latency grows with the queue):

| Concurrent users | Wall time for 8 questions | Answers / min | Median | p95 |
|---|---|---|---|---|
| 1 | 13.0 s | 37 | 1.6 s | 2.6 s |
| 2 | 12.1 s | 40 | 3.0 s | 4.0 s |
| 4 | 12.1 s | 40 | 6.1 s | 7.1 s |
| 8 | 12.3 s | 39 | 7.3 s | 12.3 s |

These eight are short lookups (about 1.5 s of model time each). The 42-question end-to-end run, with long
cross-document answers and follow-ups, has a 5.4 s median, i.e. about 10–12 answers per minute in total. No
errors at any level; the backend stays at ~57 MB and does no work of its own.

**Cheap requests are unaffected by chat load.** With four chats in flight: `/health` 13 ms median (p95
21 ms), `/preview` 6 ms (9 ms). Alone, one process handles 100–240 requests per
second; eight concurrent clients do not add throughput (one database connection).

**Indexing blocks answers.** With extraction (removed since), uploading a 5-passage document took 132 s
(about 26 s per passage, all of it model time) and answers asked meanwhile took a median 42.5 s (max 62 s)
against 6.7 s idle and 5.9 s afterwards, because extraction and answering share the model slot. Re-embedding is cheap by comparison
(14 documents in about 25 s).

**Growth of the corpus** (synthetic chunks with real queries):

| Chunks | Database | HNSW index | Answer retrieval, no model | Sparse query |
|---|---|---|---|---|
| 1,000 | 16 MB | 2 MB | 28 ms (p95 33) | 2 ms |
| 10,000 | 70 MB | 20 MB | 52 ms (p95 72) | 17 ms |
| 50,000 | 294 MB | 98 MB | 178 ms (p95 282) | 132 ms |

Vector search stays at 2–3 ms; the full-text query grows with the corpus (132 ms at 50,000 chunks).
Indexing speed was 330–530 chunks/s without a model. (Entity linking, which grew linearly with the number
of entities, and the `/graph` payload were the other two scaling problems found; both went away with the
graph.)

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
