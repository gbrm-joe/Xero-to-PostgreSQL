# Multi-Tenant Architecture

This document describes how the Xero → Postgres sync is organised across
multiple Xero tenants.

## Overview

- **One Postgres database**, hosted by GBRM, holds data for all Xero
  tenants.
- **One schema per Xero tenant**, named `finance_<tenant_slug>`. Tenants
  currently in scope:
  - `finance_gbrm` — GBRM (also covers the Mickleson sub-unit via
    tracking categories)
  - `finance_mgl` — Millson Group Ltd
  - `finance_mgi` — MGI
- **One codebase** runs the sync. A single GitHub Actions job iterates
  over the configured tenants, refreshes each tenant's OAuth token, and
  upserts into the corresponding schema.

## The `org` schema

Tenants and business units are described in the shared `org` schema.

### `org.units`

Every limited company in the group, plus any internal sub-unit, is a
row in `org.units`. Header tables in each `finance_*` schema FK back to
this table via `unit_id`.

Relevant columns:

| Column | Notes |
| --- | --- |
| `unit_id` | Primary key. |
| `unit_type` | FK to `org.ref_unit_types`. `limited_company` for the three Xero tenants; `business_unit` for sub-units like Mickleson. |
| `xero_tenant_id` | UUID. Set for `limited_company` rows that have their own Xero tenant. NULL for sub-units that share a parent's tenant. UNIQUE. |
| `parent_unit_id` | Self-reference. Sub-units point at their parent limited company (e.g. Mickleson → GBRM). |

### `org.ref_unit_types`

Reference table with two rows:

- `limited_company` — has its own Xero tenant.
- `business_unit` — sits inside a parent's Xero tenant; attributed via
  Xero tracking categories.

## The `finance_<tenant_slug>` schemas

All three `finance_*` schemas have the same 28-table structure. New
tenants are onboarded by cloning this structure.

### Xero header tables (9)

These mirror the Xero entities the sync pulls. Each has
`unit_id INT NOT NULL` referencing `org.units(unit_id)`.

- `xero_accounts`
- `xero_bank_transactions`
- `xero_contacts`
- `xero_invoices`
- `xero_journals`
- `xero_payments`
- `xero_sales_orders`
- `xero_tax_rates`
- `xero_tracking_categories`

### Xero line tables (5)

Detail rows that FK to their header. They do not carry `unit_id` (it is
implied by the header), with one deliberate exception below.

- `xero_bank_transaction_items`
- `xero_invoice_items`
- `xero_journal_lines`
- `xero_sales_order_items`
- `xero_tracking_options` — `unit_id` is **nullable**. Used for sub-unit
  attribution: tracking options that map to a `business_unit` (e.g.
  Mickleson within GBRM) carry that unit's `unit_id`. Options that are
  not unit-attributable stay NULL.

### Internal header tables (3)

Group-internal entities that live alongside the Xero data. Each has
`unit_id INT NOT NULL`.

- `timesheets` — internal timesheet system (not from Xero Payroll).
- `bonus_calculations`
- `budgets`

### Internal line tables (3)

- `timesheet_entries`
- `budget_lines`
- `budget_line_periods`

### Project / commercial tables (4)

Moved from the legacy `projects` schema during this consolidation.
Header rows carry `unit_id`.

- `quotes`
- `profit_sharing_schemes`
- `profit_sharing_tiers`
- `ref_quote_status`

### Operational tables (4)

Per-tenant sync bookkeeping.

- `tokens` — Xero refresh token for this tenant. Refresh tokens rotate
  on every use, so they are persisted in Postgres rather than env vars.
- `sync_log`
- `sync_metadata` — last full / incremental sync timestamps per entity
  (see `JOURNAL_UPDATE_TRACKING.md`).
- `sync_progress`

## Sync strategy

- **Incremental syncs (daily)** use the Xero `If-Modified-Since` header
  against a per-tenant, per-endpoint watermark stored in
  `sync_metadata`.
- **Full resync (weekly, plus on-demand)** for journals, because the
  Xero Journals API does not support reliable change detection. See
  `JOURNAL_UPDATE_TRACKING.md`.
- **Per tenant**: refresh OAuth token using the row in
  `finance_<slug>.tokens`, write the rotated token back, then sync.

## Sub-unit attribution

Some limited companies host more than one business unit inside a
single Xero tenant (e.g. Mickleson inside GBRM). These sub-units are
attributed via Xero tracking categories:

1. The sub-unit is added to `org.units` with `unit_type = 'business_unit'`
   and `parent_unit_id` set to the parent limited company.
2. The matching tracking option in
   `finance_<parent_slug>.xero_tracking_options` carries that sub-unit's
   `unit_id`.
3. Reporting joins line tables → `xero_tracking_options` → `org.units`
   to attribute revenue / cost to the sub-unit.

## Migrations

- All schema changes are versioned migrations, applied to every
  `finance_*` schema.
- `org` schema changes are applied once.
- Never let the schemas drift. If a column is added to one
  `finance_*` schema, it is added to all of them as part of the same
  migration.

## Adding a new Xero tenant

High level (full runbook to follow alongside the sync code rewrite):

1. Add a row to `org.units` for the new limited company, including its
   `xero_tenant_id` (UUID) and `unit_type = 'limited_company'`.
2. Create a new `finance_<slug>` schema using the canonical 28-table
   structure (clone from any existing `finance_*` schema with
   `LIKE INCLUDING ALL`, then recreate FKs).
3. Bootstrap `finance_<slug>.tokens` by running the OAuth flow against
   the new tenant's Xero app. The refresh token is written into that
   table.
4. The next sync run will pick the tenant up automatically because the
   sync iterates over `org.units WHERE xero_tenant_id IS NOT NULL`.

## Secrets

Credentials live in GitHub Actions secrets. The single shared Postgres
connection is configured once. Per-tenant Xero **client ID / secret**
are still supplied as secrets; per-tenant **refresh tokens** live in
the database, not in secrets.

