# Claude's Role in This Project

## What this project does
Syncs accounting data from multiple Xero tenants into a single Postgres
database (GBRM-hosted). Each Xero tenant maps to its own schema named
`finance_<tenant_slug>`. Runs on GitHub Actions: a weekly full sync and
a daily incremental sync.

## Architecture
- **One database, many schemas.** Every Xero tenant writes to a dedicated
  `finance_<tenant_slug>` schema in the same Postgres instance. Table
  structure is identical across schemas (28 canonical tables).
- **Tenant registry.** `org.units` is the source of truth for tenants
  and sub-units. Limited companies carry an `xero_tenant_id` UUID;
  sub-units (e.g. Mickleson within GBRM) hang off via `parent_unit_id`
  and are attributed through Xero tracking categories.
- **One codebase, many tenants.** A single sync run iterates over
  `org.units WHERE xero_tenant_id IS NOT NULL`, refreshes that tenant's
  OAuth token, pulls data, and upserts into the corresponding schema.
- **Token storage.** Xero refresh tokens are persisted in
  `finance_<slug>.tokens` (not in env vars) because Xero rotates them
  on every refresh.
- **Incremental syncs** use `If-Modified-Since` against a per-tenant,
  per-endpoint watermark in `finance_<slug>.sync_metadata`. Journals
  use a weekly full resync because the Journals API does not support
  reliable change detection — see `JOURNAL_UPDATE_TRACKING.md`.

For the full schema breakdown and onboarding steps, see
`MULTI_COMPANY_SETUP.md`.

## Working conventions
- UK English in code comments, docs, and commit messages.
- TypeScript / Node, run via GitHub Actions.
- Secrets live in GitHub Actions secrets; never commit them.
- All schema changes go through versioned migrations, applied to every
  `finance_*` schema. Never let the schemas drift.
- Before making non-trivial changes, summarise the proposed change and
  wait for confirmation.
- Prefer minimal diffs. Don't refactor opportunistically.

## What I (Claude) should do
- Read the existing codebase before suggesting changes.
- Treat the current single-tenant code as the reference implementation
  to be generalised, not replaced.
- Flag any assumption that conflicts with the schema-per-tenant model.
- Ask before introducing new dependencies, services, or infra.