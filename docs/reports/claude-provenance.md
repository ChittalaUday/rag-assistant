# Claude provenance audit — Niruthi public corpus

Snapshot: 2026-10-05. Read-only audit of `fixtures/documents/` (six notes),
`fixtures/sources.json`, `fixtures/questions.json` (30 Qs) and
`docs/reports/public-sources.md`. No web fetches performed.

## 1. Per-note source attribution

All six notes carry a `Snapshot: 2026-10-05` header and `Source:` HTTPS URLs
that are present in `fixtures/sources.json`. No invented URLs found.

| Note | Listed sources | In manifest? | Suspicious factual claims |
|---|---|---|---|
| company-identity.md | S1, S2 | yes | None. Brand vs legal entity is correctly disambiguated. |
| leadership-and-products.md | S1 only | yes | None invented, but ALL leadership names, titles and product grid resolution rest on a single company self-published page. Correctly flagged as "company website statements". |
| privacy.md | S2 | yes | None. Dates, email and data categories match claim text. |
| public-programs.md | S3, S5 | yes | None. GIIF 2019 Gold Award, Kharif 2023, PMFBY and the "does not name Niruthi" disclaimer are all present. |
| technology.md | S1, S4 | yes | None. Correctly states NASA page does not reference Niruthi and does not establish endorsement/employment/license. |
| public-source-boundaries.md | S1–S5 | yes | Caspian portfolio-company claim is attributed to the company's own homepage, with the failed independent fetch (S_UNVERIFIED_CASPIAN, HTTP 403) explicitly noted and no amount asserted — this is honest attribution, not an overclaim. |

No speculative numbers, implied partnerships, contract terms, future promises,
headcount, revenue, salaries or unattributed statistics appear anywhere in the
active notes.

## 2. Questions.json ↔ notes alignment

- Q1–Q19, Q24–Q30: every `expected_keyword` I spot-checked appears literally
  in the cited `source_documents`. Cross-doc and follow-up `source_ids` match
  the cited notes' listed sources.
- Q7 ("Gold Award… 2019… Mumbai"): keywords present in `public-programs.md`;
  attribution is to the GIIF interview, i.e. a company-reported award relayed
  by GIIF, not an independently audited citation. Acceptable given the note
  frames it as "GIIF reports".
- Q13, Q28 (brand vs legal name): both cite `company-identity.md` +
  `privacy.md`; Q28 additionally cites `public-source-boundaries.md` and lists
  all five source IDs. Mild over-attribution but not wrong.
- Q18, Q29, Q30: cite all five source IDs for conflict/awareness checks.
  Harmless, keywords are grounded in `public-source-boundaries.md`.
- Q20–Q23 (headcount, revenue, salary, leave): `type: "unknown"`, empty
  `expected_keywords`, empty `source_documents`, empty `source_ids`. The
  expected behavior (abstention) is noted in `public-sources.md` para 2 but
  the scoring rubric is NOT defined inside `questions.json` itself — any
  evaluator must know empty-keyword-list ⇒ score abstention. Worth flagging
  for whoever wires the scorer.
- Q24 ("What is her role at Niruthi?"), Q25, Q26, Q27: follow-ups rely on
  prior-turn pronoun resolution. `followup_of` IDs (14, 2, 6, 4) all exist.
  Behavior under a stateless evaluator is undefined — flag.

## 3. Fictional-corpus leakage

None. Grep for `northstar|acme|signal corp|elara` across `fixtures/` outside
`archive/` returns zero matches. The seven Northstar files are confined to
`fixtures/archive/northstar/`.

## 4. Honest flags — cannot verify without web fetches

I would want to confirm by direct retrieval:

- https://niruthi.com/ — leadership roster (eight named individuals, exact
  titles), the five `*Snap` product names, the "2 km by 2 km" grid, the
  "deep tech / climate risk mitigation" tagline, and the Caspian portfolio
  claim. Everything in `leadership-and-products.md` traces to this one page.
- https://niruthi.com/privacy.html — "1 March 2024" last-updated date, the
  seven-working-day account-deletion commitment, `contact@niruthi.com`.
- https://www.globalinclusiveinsuranceforum.org/multimedia/niruthi-how-does-niruthi-uses-technology-improve-livelihoods-smallholder-farmers
  — the 31 August 2021 date, that Milesi was described as "Director" and
  Kukunuri as "Chief Executive" at that time, and the Gold Award wording.
- https://www.earthdata.nasa.gov/about/competitive-programs/access/terrestrial-observation-prediction-system
  — PI attribution to "Ramakrishna Nemani" at Ames, deployment at NEX, and
  the three application domains listed.
- https://pib.gov.in/PressReleasePage.aspx?PRID=2085179 — 17 Dec 2024 date,
  Kharif 2023 reference, YES-TECH acronym expansion, and that the release
  genuinely does not name Niruthi.

Minor: `public-sources.md` lists a tentative PRID1944085 that 404'd and a
GIIF PDF not parsed. Those are documented as non-retained, so they do not
pollute the active manifest, but anyone re-running the fetch should expect
the same failures.

Overall: the corpus is tight. Attribution, hedging and the brand/legal split
are handled carefully; no fiction leaked in; the main residual risk is that
every leadership and product fact rests on one self-published page.
