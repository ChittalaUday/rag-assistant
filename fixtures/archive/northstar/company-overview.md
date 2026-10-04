# Northstar Signal — Company Overview

> Fictional reference document. All names, customers, and figures are invented for test fixtures.

Northstar Signal, Inc. is a privately held industrial telemetry company founded in Oslo in 2019 by Priya Venkatesan (CEO) and Marcus Wu (CTO). The company builds hardware and software for monitoring heavy industrial fleets: shipping, mining, utilities, and oil & gas. Headquarters moved to Rotterdam in 2022; a secondary office opened in Austin, Texas in late 2023.

Northstar closed a $42M Series B on 2024-03-10 led by Northfield Capital, with participation from Harbor Point Ventures. Headcount at the end of Q3 2026 was 148 across engineering, product, go-to-market, and operations.

The product portfolio is anchored by three pillars:

- **Beacon** — ruggedized telemetry gateway (hardware + firmware). Owned by Mira Chen since the v1 launch in 2021.
- **Pulse** — customer-facing SaaS dashboard, led by Yuki Tanaka.
- **Relay** — edge streaming service that moves data from Beacon devices into Pulse and partner systems.

Executive team (as of this document, last revised 2026-02):

- Priya Venkatesan — CEO
- Marcus Wu — CTO
- Elena Rossi — VP Engineering
- Jonas Keller — Head of Security
- Aisha Patel — Head of Customer Success
- Hana Park — Head of People

Primary infrastructure runs on AWS (us-east-1 primary, us-west-2 warm standby). Observability is standardized on Datadog; analytical workloads land in Snowflake. Identity and access are managed through Okta, with CrowdStrike Falcon on all employee laptops.

The company's largest customers by ARR are Mercator Shipping, Parallax Grid Co., and Ironhide Mining; the latter two were onboarded through the Delta-V Integrators partnership. Hardware sensors are co-developed with SensorWare GmbH in Munich. Satellite comms for over-water and remote-site deployments are provided by OrbitLink.

Northstar maintains SOC 2 Type II (most recent report issued 2026-07-01) and is actively pursuing ISO 27001 certification, with Vanta as the compliance automation vendor. Engineering tickets and roadmap items are tracked in Linear; source lives in GitHub Enterprise.

Note: This overview is reviewed semi-annually and reflects ownership as of the last revision date. For current product ownership and recent organizational changes, consult the decision log, which is authoritative.
