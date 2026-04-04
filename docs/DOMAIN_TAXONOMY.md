# Skill Domain Taxonomy

Hassaleh organizes capabilities and skills into hierarchical dot-notation domains. The domain string lives on `Capability.domain`, supports prefix discovery, and is the basis for domain-scoped agent permissions.

## Top-Level Domains

- `orchestration`
- `data`
- `security`
- `communication`
- `reporting`
- `system`
- `development`
- `integration`
- `analysis`
- `automation`

## Hierarchy

### `orchestration`

- `orchestration.rules`: Rule authoring, compilation, and policy management
- `orchestration.scheduling`: Schedules, timers, and execution windows
- `orchestration.lifecycle`: Agent, task, and intent lifecycle control

### `data`

- `data.graph`: Graph modeling, traversal, and topology operations
- `data.etl`: Data ingestion, normalization, and migration
- `data.query`: Read-oriented queries, report lookups, and discovery

### `security`

- `security.auth`: Authentication, identity, and trust boundaries
- `security.audit`: Audit exports, evidence gathering, and traceability
- `security.permissions`: Capability scoping, access control, and policy checks

### `communication`

- `communication.messaging`: Inter-agent and operator messaging
- `communication.notifications`: Informational outbound notifications
- `communication.alerts`: Urgent alerts and escalation flows

### `reporting`

- `reporting.activity`: Activity reports and operational summaries
- `reporting.metrics`: KPI, trend, and metrics reporting
- `reporting.export`: Export to JSON, Markdown, CSV, or downstream systems

### `system`

- `system.health`: Health checks, diagnostics, and status validation
- `system.maintenance`: Cleanup, housekeeping, and maintenance workflows
- `system.config`: Configuration inspection and managed updates

### `development`

- `development.testing`: Test execution and validation workflows
- `development.review`: Code review, rule review, and quality gates
- `development.ci`: CI pipeline hooks and build orchestration

### `integration`

- `integration.openclaw`: OpenClaw bridge actions and notifications
- `integration.api`: External API adapters and service calls
- `integration.webhook`: Webhook consumers and producers

### `analysis`

- `analysis.finance`: Financial analysis and portfolio-oriented tasks
- `analysis.security`: Security posture and threat analysis
- `analysis.performance`: Latency, throughput, and performance diagnostics

### `automation`

- `automation.tasks`: Task automation and repetitive action batching
- `automation.workflows`: Multi-step orchestration flows
- `automation.cron`: Cron-style schedules and recurring jobs

## Matching Rules

- Domain filtering uses prefix semantics.
- `orchestration` matches `orchestration.rules` and `orchestration.lifecycle`.
- `orchestration.rules` matches only that branch.
- Capabilities without a `domain` property remain usable for backward compatibility, but they are not discoverable through domain filters.
