# Teams and Ownership

> Fictional reference document. All names are invented for test fixtures.

Engineering and operations teams at Northstar Signal, with current ownership and reporting lines. Last revised 2026-07-02.

## Teams

- **Platform** — Led by Elena Rossi (VP Engineering, also acting Platform head). Responsible for Pulse backend services, API gateway, and shared libraries. Six engineers.
- **Firmware** — Led by Rafael Costa. Owns Beacon firmware, bootloader, and the OTA update pipeline. Four engineers plus one firmware QA.
- **Data** — Led by Noor Al-Fahim. Owns the Harbor pipeline (Snowflake ingest and archival), the Lantern alerting engine, and the internal analytics layer. Five engineers.
- **SRE** — Led by Tomás Herrera. Owns production runbooks, Datadog monitors, incident response, and the on-call rotation. Three engineers.
- **Security** — Led by Jonas Keller. Owns Okta administration, CrowdStrike endpoint deployment, vendor security review, and SOC 2 evidence collection in Vanta. Two engineers and one GRC analyst.
- **Product** — Led by Yuki Tanaka (Pulse PM). Yuki is directly responsible for Pulse roadmap and release coordination. A second PM slot for Beacon has been open since Q2 2026.
- **Customer Success** — Led by Aisha Patel. Four CSMs covering named-account tiers.
- **People Ops** — Led by Hana Park. Two generalists.

## Named product ownership (as of 2026-07-02)

- **Beacon** — Mira Chen (per legacy ownership record; see decision log for any recent change).
- **Pulse** — Elena Rossi (engineering), Yuki Tanaka (product).
- **Relay** — Noor Al-Fahim.
- **Harbor** — Noor Al-Fahim.
- **Lantern** — Dev Shah (took ownership on 2026-04-11 after shipping the dedupe rework).
- **Compass** (internal admin tool) — unowned since 2026-06; informally maintained by SRE on a best-effort basis.

## On-call

SRE holds primary; Platform rotates secondary; Firmware is paged only for Beacon device-side incidents. Rotation is managed in PagerDuty with weekly handoff on Mondays 10:00 CET.

## Reporting lines

Elena Rossi, Rafael Costa, Noor Al-Fahim, Tomás Herrera, Jonas Keller, Yuki Tanaka, and Aisha Patel all report to Marcus Wu (CTO). Hana Park reports to Priya Venkatesan (CEO).

## Hiring in flight

- Beacon PM (open since Q2 2026)
- Second firmware engineer (open since 2026-08)
- Staff security engineer approved for Q4 2026
