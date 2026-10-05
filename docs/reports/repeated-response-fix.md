# Repeated response investigation

Verified on 2026-10-05 with local qwen3.5:9b and all-minilm:l6-v2.

The frontend sends each current question and renders its authoritative API result; no canned frontend answer or substituted question was found. Before the change, CEO and WeatherSnap already produced distinct valid answers. Leave policy was unavailable from the six real public notes.

The brand/legal-name question reproduced the generic fallback despite retrieved company-identity evidence. Capturing actual model output showed a correct textual answer, no citation_ids key, and a source chunk UUID incorrectly included in relation_ids. The permissive schema defaulted missing citation_ids to an empty list, and the citation guard suppressed the answer using a generic no-evidence message. This falsely suggested the requested facts were absent.

The schema now requires answer/citation_ids/relation_ids. Sources use short S1/S2 aliases with allowed-value enums and validated mapping to real stored chunk IDs; relationship IDs remain separately constrained. Missing required fields become an explicit model error. Empty citation lists yield a visible model-verification warning, never an uncited fabricated answer. After the fix the same brand/legal question, CEO and WeatherSnap returned different, relevant cited answers in real API calls.

The demo now combines six verified public Niruthi notes with five fictional testing documents covering leave, projects, teams, working days, attendance, confidentiality, devices and employee conduct. Every synthetic paragraph marks its scope; these are not actual Niruthi policies. Public URLs and facts remain unchanged. Twelve testing questions and additional natural-language probes are recorded separately in mixed-evaluation.json.

Final quality gates: 37 Python tests, 32 frontend tests, 84% Python coverage, Ruff/format, mypy, frontend lint/typecheck and production build pass. The final live checks in iteration-live-checks.json supersede the earlier mixed-evaluation.json for public/fictional scope and labeling behavior. Explicit actual Niruthi requests exclude fictional fixtures, that scope persists across follow-ups, and fictional citations receive a deterministic notice.

Full-document reader text matched all eleven fixture files exactly. A disposable upload verified new full-text persistence, actual ingestion stages, model queueing and conversation SSE stages; it was deleted afterward. Graph evidence validation found zero invalid stored quotes/endpoints/labels. Streaming displays answer segments after complete model generation and citation validation, rather than exposing unvalidated model tokens.

The Animate UI redesign includes source/full-document dialogs, tabs, tooltips, accordion, buttons and actual-count progress. Final integration fixes place dialogs above the overlay and respect reduced motion for chat scrolling. JSDOM's missing PointerEvent, ResizeObserver and scrollTo browser APIs have test-only shims. Build reports a nonfatal 611 kB main-chunk advisory (189 kB gzip); Sigma is loaded separately. FastAPI TestClient emits an upstream httpx deprecation warning.

User authorized browser verification, but the browser tool still rejected local preview access under a saved site-blocking preference. No alternate browser route was attempted. Schema and exact quote validation do not prove semantic entailment; continue inspecting real answers and model warnings. The historical 30-question smoke score is not a new accuracy claim for this iteration.
