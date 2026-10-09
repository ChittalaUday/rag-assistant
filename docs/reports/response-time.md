# Response time: per-stage baseline (2026-10-09)

Where the time goes when the assistant answers a question, measured stage by stage on 49 questions
of 11 kinds. This is the baseline for the response-time work: every change is measured against it
with `make eval-latency`.

## Summary

- **A typical answer takes 10.3 s (p50); one in ten takes over 22 s (p90); the slowest took 35.5 s.**
  Small talk takes about 1 s.
- **Three model steps make up nearly all of it:** answer generation (p50 4.2 s), statement
  verification (p50 3.3 s) and reranking (p50 2.9 s). Routing, query embedding, search and citation
  validation each take under 0.1 s.
- **Answer length drives the spread.** Total time ≈ 7.2 s + 27 ms per output token
  (generation and verification together), correlation 0.97. Lists and multi-hop answers are slow
  because they are long, and verification grows with them (p90 10.2 s).
- **Cold start:** the first question took 19.4 s, 6.2 s of it loading the reranker. Ollama unloads idle
  models after 5 minutes, so the first question after a pause pays this again.
- Accuracy in this run: 41 of 45 document questions correct; no answer needed a retry.

## Setup

| | |
|---|---|
| Machine | Apple M5 Pro, 24 GB unified memory, macOS 27.0 |
| Ollama | 0.35.1, all models local |
| Answer model | qwen3.5:9b (thinking off) |
| Embedding model | embeddinggemma (768-d) |
| Reranker | Qwen3-Reranker-0.6B (F16), 30 candidates, 4 scored at a time |
| Settings | defaults: statement verification on, second hop always, contextual chunk summaries on, structure chunker, 9,000-character evidence budget, small-talk margin 0.11 |
| Code | commit 9896220 plus the uncommitted stage-timing changes |
| Corpus | fixtures/documents, fixtures/testing, fixtures/archive/northstar, fixtures/multilingual, indexed in a scratch schema (133 s) |
| Questions | `fixtures/latency-questions.json`: all 30 of `questions.json` (single, cross-document, unknown, conflicting, follow-ups with their parents), 3 list, 4 multi-hop, 8 Indian-language (native, cross-language, Indian-language about English documents), 4 small talk |
| Mode | one question at a time, through the query pipeline in process (no HTTP, not streamed) |

Stage times come from the pipeline's own stage markers (`metrics.stages_ms` on every saved answer):
each stage runs from its start to the next stage's start. Model usage comes from Ollama's counters
for the question being answered (`metrics.usage`).

## Total and per-stage distribution

All 48 questions after the first (the first also loads models), in seconds. Stages that only some
questions have (small-talk reply, follow-up rewrite) show their own n; with fewer than 10 values only
p50 is given, because higher percentiles would just repeat the slowest one.

| Measure | min | mean | stdev | p50 | p75 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|---|---|
| **Total** | 0.8 | 12.2 | 6.8 | 10.3 | 15.1 | 22.1 | 24.2 | 31.1 | 35.5 |
| Routing (embedding) (n=48) | 0.0 | 0.1 | 0.0 | 0.0 | 0.1 | 0.1 | 0.1 | 0.1 | 0.1 |
| Small-talk reply (n=4) | 0.8 | 1.1 | 0.4 | 1.1 | – | – | – | – | 1.6 |
| Follow-up rewrite (n=4) | 0.6 | 0.9 | 0.3 | 1.0 | – | – | – | – | 1.2 |
| Query embedding (n=44) | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| Search (dense + keyword) (n=44) | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| Reranking (n=44) | 0.9 | 2.7 | 1.2 | 2.9 | 3.3 | 3.7 | 4.7 | 5.5 | 5.7 |
| Answer generation (n=44) | 2.9 | 5.4 | 2.5 | 4.2 | 6.3 | 9.5 | 10.2 | 12.4 | 14.0 |
| Citation validation (n=44) | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| Statement verification (n=44) | 2.4 | 5.0 | 3.5 | 3.3 | 6.1 | 10.2 | 11.8 | 15.9 | 18.8 |

## Per question type

Total seconds. n is small for most types; read these as ranges, not as precise percentiles.

| Type | n | min | mean | stdev | p50 | p75 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|---|---|---|
| list | 3 | 13.7 | 25.1 | 10.9 | 26.2 | – | – | – | – | 35.5 |
| multihop | 4 | 17.3 | 20.9 | 3.1 | 20.8 | – | – | – | – | 24.6 |
| crossdoc | 7 | 7.7 | 15.1 | 4.7 | 15.9 | – | – | – | – | 22.8 |
| conflict | 3 | 10.0 | 15.4 | 7.1 | 12.9 | – | – | – | – | 23.4 |
| crosslingual | 2 | 11.4 | 12.1 | 0.9 | 12.1 | – | – | – | – | 12.8 |
| followup | 4 | 9.4 | 10.7 | 1.0 | 11.0 | – | – | – | – | 11.5 |
| native | 4 | 9.6 | 11.6 | 2.4 | 11.0 | – | – | – | – | 14.9 |
| unknown | 4 | 8.8 | 11.8 | 4.3 | 10.1 | – | – | – | – | 18.1 |
| indic-on-english | 2 | 9.8 | 9.9 | 0.2 | 9.9 | – | – | – | – | 10.0 |
| single | 11 | 7.0 | 8.3 | 1.0 | 8.3 | 8.9 | 9.8 | 9.9 | 10.0 | 10.0 |
| small-talk | 4 | 0.8 | 1.2 | 0.4 | 1.1 | – | – | – | – | 1.6 |

Median seconds per stage for each type (– where the stage does not run or takes under 0.05 s):

| Type | n | Rewrite | Rerank | Generate | Verify | Total |
|---|---|---|---|---|---|---|
| list | 3 | – | 3.0 | 10.4 | 12.1 | 26.2 |
| multihop | 4 | – | 3.0 | 9.1 | 8.8 | 20.8 |
| crossdoc | 7 | – | 2.9 | 5.7 | 6.1 | 15.9 |
| conflict | 3 | – | 3.4 | 5.1 | 4.3 | 12.9 |
| crosslingual | 2 | – | 4.8 | 3.9 | 3.3 | 12.1 |
| followup | 4 | 1.0 | 3.3 | 3.6 | 2.8 | 11.0 |
| native | 4 | – | 3.1 | 4.3 | 3.4 | 11.0 |
| unknown | 4 | – | 3.1 | 3.7 | 2.6 | 10.1 |
| indic-on-english | 2 | – | 2.9 | 3.9 | 3.0 | 9.9 |
| single | 11 | – | 1.0 | 4.0 | 2.7 | 8.3 |
| small-talk | 4 | – | – | – | – | 1.1 |

## Model usage per question

From Ollama's counters for each question. Generate covers every chat-model call (answer, statement
verification, follow-up rewrite, small-talk reply), so its output tokens are the answer plus the
verification output. Rerank `model_ms` adds up 30 calls that run 4 at a time, so the wall-clock rerank
stage is about a quarter of it.

| Model call | Measure | min | mean | stdev | p50 | p75 | p90 | p95 | p99 | max |
|---|---|---|---|---|---|---|---|---|---|---|
| generate | calls | 1 | 2 | 0 | 2 | 2 | 2 | 3 | 3 | 3 |
| generate | prompt_tokens | 139 | 3583 | 1190 | 3645 | 4067 | 4678 | 5062 | 5227 | 5237 |
| generate | output_tokens | 16 | 208 | 217 | 108 | 257 | 528 | 636 | 904 | 1088 |
| generate | model_ms | 762 | 9676 | 6213 | 7497 | 11190 | 18934 | 21653 | 27897 | 32730 |
| generate | load_ms | 0 | 3 | 2 | 2 | 3 | 4 | 6 | 9 | 9 |
| embed | calls | 1 | 2 | 0 | 2 | 2 | 2 | 2 | 2 | 2 |
| embed | prompt_tokens | 10 | 43 | 14 | 42 | 50 | 58 | 63 | 73 | 74 |
| embed | output_tokens | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| embed | model_ms | 13 | 48 | 21 | 44 | 54 | 70 | 88 | 115 | 128 |
| embed | load_ms | 1 | 3 | 2 | 3 | 3 | 6 | 7 | 10 | 11 |
| rerank | calls | 30 | 30 | 0 | 30 | 30 | 30 | 30 | 30 | 30 |
| rerank | prompt_tokens | 7054 | 8350 | 1284 | 7892 | 8786 | 10620 | 11390 | 11520 | 11563 |
| rerank | output_tokens | 30 | 30 | 0 | 30 | 30 | 30 | 30 | 30 | 30 |
| rerank | model_ms | 3274 | 10063 | 4710 | 11117 | 12705 | 14090 | 17976 | 21200 | 21997 |
| rerank | load_ms | 27 | 39 | 10 | 36 | 43 | 51 | 55 | 67 | 68 |

Generation runs at roughly 16 output tokens per second including prompt processing; prompts are
about 3,600 tokens (p50) across the calls of one question.

## Every question

Seconds; Other is routing, embedding, search and validation together (or the whole small-talk reply).
Output tokens are those of all chat-model calls for the question.

| # | Type | Question | Total | Rewrite | Rerank | Generate | Verify | Other | Output tokens | Result |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | single | How does Niruthi describe itself on its homepage? | 19.4 | – | 2.6 | 8.2 | 7.7 | 0.9 | 484 | correct |
| 2 | single | Who is listed as the Founder of Niruthi? | 8.8 | – | 1.1 | 4.2 | 3.4 | 0.1 | 70 | wrong |
| 3 | single | Who is listed as CEO of Niruthi on the homepage? | 9.0 | – | 1.0 | 4.3 | 3.5 | 0.1 | 120 | correct |
| 4 | single | What is WeatherSnap and at what spatial resolution does the homepage … | 8.3 | – | 1.4 | 3.9 | 2.9 | 0.1 | 81 | correct |
| 5 | single | Which NASA system does Niruthi's homepage say its approach is derived… | 9.8 | – | 0.9 | 6.2 | 2.6 | 0.1 | 84 | correct |
| 6 | single | Who is listed as the Principal Investigator of NASA TOPS? | 7.7 | – | 1.0 | 3.9 | 2.6 | 0.1 | 84 | correct |
| 7 | single | What recognition from GIIF does the public interview describe Niruthi… | 7.6 | – | 1.1 | 3.7 | 2.7 | 0.1 | 93 | correct |
| 8 | single | What is Niruthi's registered legal entity name? | 7.9 | – | 1.1 | 4.0 | 2.6 | 0.1 | 95 | correct |
| 9 | single | When was the Niruthi privacy policy last updated? | 7.0 | – | 0.9 | 3.5 | 2.4 | 0.1 | 84 | correct |
| 10 | single | What email contact is published on the Niruthi privacy policy? | 7.1 | – | 1.0 | 3.4 | 2.6 | 0.1 | 81 | correct |
| 11 | single | What categories of data does the Niruthi privacy policy describe coll… | 10.0 | – | 0.9 | 5.1 | 3.9 | 0.1 | 190 | correct |
| 12 | single | Which Indian ministry's press release confirms YES-TECH under PMFBY? | 8.7 | – | 1.2 | 4.2 | 3.3 | 0.1 | 108 | correct |
| 13 | crossdoc | How does Niruthi's public-facing brand name differ from its registere… | 7.7 | – | 1.1 | 3.9 | 2.6 | 0.1 | 103 | correct |
| 14 | crossdoc | Which Niruthi leaders were interviewed by GIIF and what roles does th… | 22.8 | – | 1.1 | 9.8 | 11.8 | 0.1 | 601 | correct |
| 15 | crossdoc | What is the relationship between NASA TOPS and the technology Niruthi… | 15.9 | – | 2.6 | 7.0 | 6.1 | 0.2 | 273 | wrong |
| 16 | crossdoc | Which Niruthi products relate to the crop monitoring and yield foreca… | 17.1 | – | 3.7 | 7.2 | 6.1 | 0.1 | 300 | correct |
| 17 | crossdoc | How does GIIF characterize Niruthi, and where does its privacy policy… | 13.1 | – | 3.3 | 5.1 | 4.6 | 0.1 | 229 | correct |
| 18 | crossdoc | What does the PIB press release say about YES-TECH, and how is Niruth… | 12.0 | – | 2.9 | 5.3 | 3.7 | 0.1 | 167 | correct |
| 19 | crossdoc | Which of the sources used in this collection are primary government o… | 16.8 | – | 2.9 | 5.7 | 8.2 | 0.1 | 335 | correct |
| 20 | unknown | What is Niruthi's current headcount? | 9.1 | – | 3.4 | 3.1 | 2.6 | 0.1 | 71 | correct |
| 21 | unknown | What is Niruthi's annual revenue? | 11.0 | – | 4.8 | 3.6 | 2.6 | 0.1 | 77 | correct |
| 22 | unknown | What is the average salary at Niruthi? | 8.8 | – | 2.5 | 3.8 | 2.4 | 0.1 | 109 | correct |
| 23 | unknown | What is Niruthi's employee leave policy? | 18.1 | – | 2.9 | 7.8 | 7.4 | 0.1 | 434 | wrong |
| 24 | followup | What is her role at Niruthi? | 11.5 | 1.2 | 3.2 | 4.2 | 2.9 | 0.1 | 134 | correct |
| 25 | followup | What company did he found? | 9.4 | 0.6 | 3.4 | 2.9 | 2.4 | 0.1 | 93 | correct |
| 26 | followup | Which research center is named alongside him in that program source? | 10.5 | 0.9 | 3.6 | 3.2 | 2.7 | 0.1 | 119 | correct |
| 27 | followup | At what spatial grid does it provide data? | 11.5 | 1.1 | 3.0 | 4.0 | 3.3 | 0.1 | 97 | correct |
| 28 | conflict | Does Niruthi use one name for its public brand and a different name f… | 12.9 | – | 3.4 | 5.1 | 4.3 | 0.1 | 145 | correct |
| 29 | conflict | Can the company marketing claims or displayed certification badges be… | 23.4 | – | 3.4 | 9.5 | 10.4 | 0.1 | 650 | correct |
| 30 | conflict | The privacy policy is dated 1 March 2024. Can we treat it as current … | 10.0 | – | 3.3 | 3.7 | 2.9 | 0.1 | 120 | correct |
| 31 | list | Who are the leaders at Niruthi? | 26.2 | – | 3.6 | 10.4 | 12.1 | 0.1 | 697 | correct |
| 32 | list | Which compliance regimes is Northstar not subject to? | 13.7 | – | 3.0 | 4.4 | 6.2 | 0.1 | 252 | correct |
| 33 | list | Which roles is Northstar trying to fill at the moment? | 35.5 | – | 2.7 | 14.0 | 18.8 | 0.1 | 1088 | wrong |
| 34 | multihop | Who does the lead of the Firmware team report to? | 19.9 | – | 3.1 | 9.4 | 7.4 | 0.1 | 400 | correct |
| 35 | multihop | Who leads the team that owns the Harbor pipeline? | 24.6 | – | 2.8 | 10.3 | 11.4 | 0.1 | 611 | correct |
| 36 | multihop | Which team handles vendor security review, and who leads that team? | 21.8 | – | 3.0 | 8.7 | 9.8 | 0.2 | 497 | correct |
| 37 | multihop | Who is the current engineering owner of Beacon, and who held that own… | 17.3 | – | 3.0 | 6.5 | 7.7 | 0.1 | 338 | correct |
| 38 | native | जयपुर ज़िला जल सहकारी समिति के कितने सदस्य हैं? | 12.0 | – | 3.0 | 4.9 | 3.9 | 0.1 | 158 | correct |
| 39 | native | जयपुर जल सहकारी समिति की अध्यक्ष कौन हैं? | 14.9 | – | 5.7 | 5.4 | 3.8 | 0.1 | 149 | correct |
| 40 | native | নদিয়া তাঁতশিল্পী সমবায়ে কতজন তাঁতি সদস্য আছেন? | 10.0 | – | 3.2 | 3.7 | 3.0 | 0.1 | 90 | correct |
| 41 | native | নদিয়া তাঁতশিল্পী সমবায়ের সভাপতি কে? | 9.6 | – | 3.1 | 3.7 | 2.7 | 0.1 | 79 | correct |
| 42 | crosslingual | What is the annual membership fee of the Jaipur district water cooper… | 11.4 | – | 4.2 | 3.7 | 3.3 | 0.1 | 81 | correct |
| 43 | crosslingual | What is the maximum loan a member of the Nadia weavers' cooperative c… | 12.8 | – | 5.3 | 4.0 | 3.3 | 0.1 | 87 | correct |
| 44 | indic-on-english | Northstar Signal में साल 2026 की तीसरी तिमाही के अंत में कितने कर्मचा… | 9.8 | – | 2.9 | 3.8 | 2.9 | 0.1 | 94 | correct |
| 45 | indic-on-english | நார்த்ஸ்டார் நிறுவனத்தின் Series B நிதி எந்த தேதியில் முடிந்தது? | 10.0 | – | 2.9 | 4.0 | 3.1 | 0.1 | 98 | correct |
| 46 | small-talk | hello | 1.1 | – | – | – | – | 1.1 | 16 | reply |
| 47 | small-talk | thanks! | 0.8 | – | – | – | – | 0.8 | 20 | reply |
| 48 | small-talk | who are you? | 1.1 | – | – | – | – | 1.1 | 31 | reply |
| 49 | small-talk | నమస్కారం | 1.6 | – | – | – | – | 1.6 | 52 | reply |

## What this suggests (not yet measured)

1. **Return the answer before statement verification.** The answer and its checked citations are
   ready after generation; per-sentence support could arrive afterwards. Would save the whole
   verification stage from the wait (p50 3.3 s, p90 10.2 s) without changing any answer.
2. **Fewer rerank candidates.** 30 to about 12–15 could roughly halve the 2.9 s stage if retrieval
   recall holds (`make eval-retrieval` measures it without the chat model).
3. **Cheaper verification.** Matching sentences to passages by embeddings would take milliseconds
   instead of seconds, but it decides which statements show as unsupported, so it needs its own
   accuracy check.
4. **Keep models loaded.** A longer keep-alive removes the cold start (6.2 s of reranker load) at the
   cost of holding the models in memory (about 7.7 GB together).
5. **Shorter answers or a smaller evidence budget.** Fewer tokens are the most direct lever (27 ms per
   output token) but the riskiest for accuracy.

## Limitations

- One run on one machine, questions answered one at a time with no other load; concurrent users wait
  for the single model slot, which this does not show.
- Not streamed: in the app the answer appears as a draft while it is generated, so the wait before
  text appears is shorter than these totals (recorded as `metrics.first_draft_ms` on streamed answers).
- Few questions per type (2 to 11); per-type figures are indicative.
- Small talk shows as wrong in the harness's accuracy score only because the scorer has no small-talk
  category; all four got a reply.

## Reproduce

```sh
make eval-latency NAME=my-run          # ~10 min with models loaded; writes work/latency/my-run.json
PYTHONPATH=backend uv run --no-sync python -m knowledge_engine.evaluation latency work/latency/my-run.json
```

The baseline results are in `work/latency/baseline-2026-10-09.json` (git-ignored, on this machine).
