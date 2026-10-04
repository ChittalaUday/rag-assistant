# Decision Log

> Fictional reference document. All entries are invented for test fixtures.

Northstar Signal engineering and operating decisions. Reverse chronological. Entries in this log are canonical over any older ownership listed elsewhere.

## 2026-09-30 — Harbor cost reduction scope

Noor Al-Fahim will target a 30% monthly Snowflake spend reduction by tiering telemetry partitions older than 30 days to external tables backed by S3. Finance (Hana Park standing in while the controller role is vacant) signed off on a 2-month overrun of the current Snowflake commit while the tiering lands.

## 2026-09-18 — Beacon engineering ownership reassigned to Dev Shah

Mira Chen stepped off the Beacon product line to lead a new confidential R&D initiative reporting directly to Marcus Wu. Engineering ownership of Beacon — covering firmware roadmap, hardware liaison with SensorWare GmbH, and the Beacon v3.1 project — transfers to Dev Shah effective 2026-09-21. This supersedes the ownership listed in the company overview and the teams-and-ownership document; both will be updated at the next semi-annual revision. Jonas Keller retains firmware signing chain approval authority.

## 2026-09-02 — Compass retirement approved in principle

Compass (internal admin tool, Rails 6) will be sunset. No new features will be merged. A replacement is not yet scoped. SRE will continue best-effort maintenance until a cutover plan is approved. Tracked as Linear ticket COMP-414.

## 2026-07-01 — SOC 2 Type II renewal issued

Report covers 2025-07-01 through 2026-06-30. One minor exception noted: a 9-day delay in deprovisioning a departed contractor's Snowflake user. Remediation complete; Vanta control updated.

## 2026-05-20 — Pulse 2.0 GA

Shipped on schedule. Rollback plan retained for 30 days, then retired per policy.

## 2026-04-11 — Lantern dedupe rework shipped

Dev Shah delivered the alert deduplication rework. This also established Dev Shah as the engineering owner for Lantern going forward.

## 2026-03-14 — OrbitLink multi-year renewal

Signed through 2028-06. Pricing locked at 2024 rates in exchange for a reference-customer commitment.

## 2026-02-10 — Austin office lease extended

Through 2029-01. No change to Rotterdam headquarters.

## 2025-11-04 — Beacon v3 shipped

First production deployments at Mercator Shipping and Parallax Grid Co.

Older entries archived in Linear project OPS-DECISIONS.
