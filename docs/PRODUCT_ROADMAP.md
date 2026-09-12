# Product Readiness

Status: internal LAN pilot, not ready for paid public access.

## Product Boundary

Sell a managed property search with explicit criteria, saved shortlists, change
history and evidence-based recommendations. A context is a user-owned search
configuration; a collector job is an operational source adapter. Do not equate a
paid context with an independent browser scraping job. Compatible customer searches
should reuse permitted source acquisition while keeping preferences, selections,
reviews and billing private.

Pricing is undecided: active-context subscription versus a one-time additional
context purchase. Do not enable payments until entitlement semantics, cancellation,
refunds and ongoing collection costs have been agreed.

## Release Gates

### 1. Private Pilot Foundation

- Maintain server-side ownership checks on every read and write, including grouped
  source listings, map summaries and AI results. Test with multiple non-admin users.
- Make new context activation explicit. Today UI-generated searches are disabled
  and the scheduler loads YAML searches; creating a context does not start collection.
- Separate admin diagnostics and grouping operations from customer navigation.
- Offer maintenance preview, backup and cleanup reports. Legacy unassigned data
  must be attributed or explicitly approved for deletion, not deleted heuristically.
- Make fresh SQLite installation, upgrades and backup restoration reproducible.
  Early Alembic revisions remain a blocker for clean installation.

Acceptance: two pilot accounts complete create/search/review/delete workflows without
admin intervention or cross-account data exposure; an empty install and a restored
backup pass the same tests.

### 2. Reliable Search And AI

- Persist analysis jobs with owner, context revision, progress, cancellation,
  bounded retries and per-user concurrency limits. Work must survive a closed tab
  and process restart; the current browser-driven sequential loop does not.
- Show source freshness, disabled or blocked collection and missing evidence.
- Tie nearby transport and amenities to attributable geographic data. Until then,
  AI must mark unsupported proximity claims as questions, not verified advantages.
- Add side-by-side comparison of a final shortlist and evaluation cases for AI
  hallucinations, stale reviews and conflicting requirements.

Acceptance: a slow or unavailable model does not block other users; progress and
results survive restart; changing context criteria invalidates pending results.

### 3. Entitlements And Payment

- Model plans, purchased capacity, active entitlements and an append-only payment
  event ledger separately from user-owned contexts and source jobs.
- Enforce context and AI allowances server-side, with transactional quota checks.
- Integrate one chosen payment provider through signed, idempotent webhooks.
- Define renewal, failed payment, cancellation and refund behavior before charging.
  An expired entitlement must not silently destroy customer data.

Acceptance: duplicate and out-of-order payment events cannot double-charge, double
credit or grant access to another account; test this in provider sandbox first.

### 4. Public Launch Operations

- HTTPS, secure session configuration, login throttling, password recovery and
  administrative action audit trails.
- Error monitoring, health checks, collection freshness alerts, AI latency/cost
  metrics, backup rotation and a tested restore procedure.
- Review source usage permissions and third-party data terms for commercial use;
  define customer-facing terms, privacy and retention policies before launch.
- Load-test intended account and job volume before choosing PostgreSQL or a queue.
  Keep the LAN deployment stable while validating the production deployment.

Acceptance: documented incident recovery, restore verification, bounded operating
costs and no known critical account-isolation defects.

## Current Evidence

2026-09-12: authorized SQLite VACUUM reduced the active file from 273,068,032 to
77,352,960 bytes; counts remained 3,960 listings, 61,274 observations and one context.
Integrity and foreign-key checks passed. Backup:
`data/backups/before-vacuum-20260912T203659Z.sqlite3.gz` on the LAN server.
The 212 legacy-import listings and 27 listings without observations were not deleted.
Backups are separate disk usage and remain retained.
