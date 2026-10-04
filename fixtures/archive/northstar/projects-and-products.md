# Projects and Products

> Fictional reference document. All project names and figures are invented for test fixtures.

Current engineering projects and shipped products at Northstar Signal.

## Shipped products

- **Beacon v3** — shipped 2025-11-04. Third-generation telemetry gateway. Dual-SIM cellular with OrbitLink satellite failover, rated for -40°C to +70°C operation. Firmware is signed with a hardware root of trust co-designed with SensorWare GmbH. Approximately 11,400 units in the field across customer sites.
- **Pulse 2.0** — GA 2026-05-20. Customer dashboard rewrite. Replaced the legacy AngularJS frontend with a React/TypeScript stack. Introduced per-tenant alert routing and the Lantern alert engine integration.
- **Relay** — edge streaming service, continuously deployed. Runs in AWS us-east-1 (primary) and us-west-2 (warm standby). Ingests roughly 2.3 billion telemetry points per day as of September 2026.
- **Harbor** — long-term telemetry archive. Hot data in Snowflake for 90 days, then compacted Parquet in S3 Glacier Deep Archive. Retention policy defined in the policies document.
- **Lantern** — alerting engine. Deduplication rework shipped 2026-04-11 by Dev Shah. Routes customer-facing alerts via Twilio (SMS, voice) and SendGrid (email).

## Active projects (Q4 2026)

- **Beacon v3.1 firmware** — adds signed delta OTA updates and reduces idle power draw by a target 18%. Target ship: 2026-12-15. Project lead was reassigned on 2026-09-18 (see decision log for the current engineering owner).
- **Pulse multi-region** — make Pulse read-active in us-west-2 to meet Parallax Grid Co.'s RPO requirement. Owned by Elena Rossi. Target: Q1 2027.
- **Harbor cost reduction** — Noor Al-Fahim leading. Goal: cut monthly Snowflake spend by 30% by tiering cold partitions. In progress.
- **Compass retirement** — the internal admin tool has been slated for retirement. Decision pending; owners have not been named. Tracked in Linear as COMP-414.
- **ISO 27001** — Jonas Keller driving. Target stage-2 audit in Q2 2027. Vanta is the automation vendor.

## Internal tools

- **Compass** — internal admin tool, Rails 6, no active maintainer.
- **Siren** — internal paging escalation helper built by SRE on top of PagerDuty webhooks.

All project tickets live in Linear; the engineering roadmap is reviewed in the first Platform standup of each month.
