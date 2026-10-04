# Policies and Controls

> Fictional reference document. All policies and vendor references are invented for test fixtures.

Northstar Signal operating policies and controls. Owners are listed where accountability is explicit.

## Change management

- All code lands via GitHub Enterprise pull requests. Each PR requires two approving reviewers; one must be from the owning team per the ownership map in the teams document.
- Any change touching authentication, secrets, Okta configuration, or the SensorWare-signed firmware signing chain requires an additional approval from Jonas Keller or his designated backup.
- Production deploys are gated by CI (Datadog deploy marker plus a synthetics check).

## Incident response

- Severity ladder: SEV1 (customer-wide outage or data loss), SEV2 (major degradation or single large customer impact), SEV3 (partial degradation), SEV4 (internal only).
- SEV1 and SEV2 pages route to the SRE primary (Tomás Herrera's team) via PagerDuty, with Platform secondary. Firmware is only paged for Beacon device-side incidents.
- SEV1 and SEV2 incidents require a written postmortem within 5 business days, reviewed by Marcus Wu.

## Access

- Single sign-on through Okta is required for all internal systems. MFA (WebAuthn preferred, TOTP fallback) is mandatory.
- CrowdStrike Falcon is deployed to all employee laptops. The Ironhide Mining bastion exception is documented in the vendor file and reviewed quarterly.
- Vendor accounts (Snowflake, Datadog, Stripe, Twilio, SendGrid, PagerDuty, Linear, GitHub Enterprise, Vanta) are provisioned via Okta where supported. Break-glass credentials are stored in a sealed envelope in the Rotterdam office safe and rotated annually.

## Data retention

- Raw telemetry: 90 days hot in Snowflake, then compacted and archived in S3 Glacier Deep Archive for 7 years.
- Application logs: 30 days in Datadog, 1 year in S3.
- Customer PII in Pulse: retained for the life of the contract plus 24 months, then purged.

## Vendor review

Every vendor with access to customer or employee data is reviewed annually by Security. The current vendor list is tracked in Vanta.

## Compliance

- SOC 2 Type II report most recently issued 2026-07-01, covering the trailing 12 months.
- ISO 27001 stage-2 audit targeted for Q2 2027.
- Northstar is not currently in scope for HIPAA, PCI-DSS, or FedRAMP.

## Open policy items

- Internal AI tool usage policy — drafted, not yet ratified. Joint owners: Hana Park and Jonas Keller.
