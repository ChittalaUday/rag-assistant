# Company Assistant contributor rules
Build locally. Keep company documents and model inference on loopback; never commit runtime data or secrets.
Use Python 3.12+, FastAPI, Pydantic; React + Vite + TypeScript, Tailwind, shadcn-style Radix components, Sigma + Graphology.
Every graph edge must have validated verbatim evidence and a source chunk. No predefined keyword graph. Never fabricate model results or silently fall back to fake answers.
Use tests for hashing/deduplication/replacement, evidence validation, retrieval, follow-ups, source selection, and failures. Run ruff, mypy, pytest, frontend lint/typecheck/tests/build before delivery.
Agents own disjoint paths; root integrates. Do not commit or push from agents. Coordinate in Linear Company Knowledge Assistant; do not touch unrelated issues.
Prefer a single monorepo. Bound extraction, upload sizes and queues. Structured output is untrusted; validate endpoints and quotes against source text. Explain limitations in docs/reports.
