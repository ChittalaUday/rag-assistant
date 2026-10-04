# Agent coordination

The user requested Codex and authenticated Claude CLI together, explicit task sharing, separate file ownership and Linear as the shared work record. Application inference and uploaded company data remain local; the hosted development CLI is separate from the Ollama runtime.

- Coordinator: repository/configuration/runbook/CI, integration, actual model evaluation, permission-dependent browser verification, GitHub publishing and final reports; UDA-48/UDA-49.
- Codex backend worker: backend/**, ingestion, persistence, model calls, evidence graph/chat and behavioral tests; UDA-45. Claude receives bounded backend tasks with the same path restrictions.
- Codex frontend worker: frontend/**, local API consumption, chat/map/evidence/document management and UI tests; UDA-46. Authenticated Claude completed the Niruthi branding/public-snapshot labels and safe source-link contribution; Codex adapted plural source URLs and verified the combined frontend.
- Codex evaluation worker with authenticated Claude CLI: fixtures/** and assigned evaluation reports/scripts, the active Niruthi public fact notes, source manifest and 30 evidence-linked questions; UDA-47. Earlier fictional fixtures and LightRAG observations remain historical evidence.

Workers own disjoint paths and report exact checks and limitations. Ownership can change only through a concrete coordinator assignment; the documentation pass owns README.md, this file and the design/plan files, while root owns progress/handoff reports. The coordinator integrates and verifies the combined implementation. Contributors do not commit or push independently. Progress and handoffs are recorded in the existing Linear Company Knowledge Assistant project; unrelated issues are not modified.

## Active data and evidence boundaries

The user redirected the starter corpus to Niruthi Climate and Ecosystems using public sources. Active loading uses six source-linked notes in fixtures/documents/ and the active questions/manifest. Public notes are dated snapshots, not internal policies or a continuously synchronized website index. Attribute company marketing claims and distinguish third-party framework/program descriptions from endorsements or contracts. Missing salaries, leave policies, headcount and revenue require abstention unless uploaded documents supply evidence.

Northstar Signal fixtures are archived separately and excluded from active loading. The LightRAG probe intentionally used fictional documents; preserve those measured results and label them as an engine smoke test, not Niruthi answer-quality evidence.

## Claude/Codex execution

Invoke the installed authenticated Claude CLI for small concrete assigned tasks, typically with `claude -p --permission-mode bypassPermissions --output-format json`. Include repository instructions, explicit allowed paths, no commits/pushes, and relevant verification. Codex reviews the actual contribution and reruns applicable checks. Preserve reported model/session/duration/usage/cost metadata in coordinator-owned reports; reported cost is not an independently estimated bill.

The runtime must never fall back to Claude/cloud inference or fabricated answers when Ollama is offline. Do not send uploaded private company documents to hosted development tools. Public-source research does not authorize unrelated account access.

## Verification and approvals

The user authorized autonomous implementation without repeated approval prompts. Browser actions remain subject to the desktop approval system: an existing denial must not be bypassed through CLI automation, another browser surface or browser commands. Unit tests, static checks and builds can proceed while browser approval is pending. Report browser tests as authored but unexecuted until permitted and actually run. Do not claim live-model scores from injected providers or transfer historical fictional probe results to the active public corpus.

## Decisions

Use the folder supplied by the user and one private repository so API contracts and quality gates stay versioned together. Select the application-owned SQLite retrieval and validated evidence graph after the LightRAG provenance probe. Keep failures and limitations visible, including rejected extraction, unavailable sources, local-model quality issues and pending verification.
