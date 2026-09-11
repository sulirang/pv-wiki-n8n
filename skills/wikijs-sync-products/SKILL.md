---
name: wikijs-sync-products
description: Install, upgrade, validate, repair, or roll back the n8n-native PV Wiki official-datasheet research workflow and its Python PDF/state Worker on a user-authorized host. Use for n8n workflow/model credentials, Exa transport, source-catalogue refresh, PDF provenance/cache, checkpoint recovery and restricted Wiki.js publication.
---

# Deploy the n8n PV Wiki automation

The current runtime is the workflow in `deploy/n8n/workflows/datasheet`.
n8n owns research decisions, every model call, page selection, extraction,
independent review, corrections and scheduling. Python provides the existing
Exa transport, PDF download/cache/parse/render, validation, persistence and
Wiki.js writing. Do not install or enable Hermes for this path.

Use the user's existing authorization and deployment choices. Ask only for
information or authority that is actually missing; this skill does not require
repeat confirmation for an already authorized deployment or repair.

Read the relevant current resources before changing a host:

- [n8n deployment](../../deploy/n8n/README.md) and
  [existing local Worker](../../deploy/n8n/LOCAL-WORKER.md);
- [research procedure](../pv-wiki-research/SKILL.md);
- [source and parameter policy](references/source-policy.md);
- [operations and recovery](references/operations.md);
- [Wiki.js setup](references/wikijs-setup.md).

Older Hermes deployment files and legacy `/run-one` workflows are retained
compatibility artifacts, not instructions to replace the current n8n runtime.
Existing database tables/classes with historical names remain implementation
compatibility; do not infer orchestration ownership from their names.

## Deployment boundaries

Work in the authorized host/project and Wiki.js prefix. Inspect the current
workflow ID, active status, node credentials, model choices, Worker service,
state/PDF volumes, source connection and backup locations before updating.
Preserve existing model providers, credentials and intentional configuration.
Keep a workflow export and state backup before replacement or migration.

Keep source catalogue, PV Wiki state and Wiki.js application databases separate.
Use a SELECT-only catalogue role and an explicit `PGSSLMODE`; preserve the
operator's established TLS configuration and document any actual server limit.
Write Wiki.js through its configured API, preserving the managed page path and
human-authored region. Do not put credentials into model prompts or source data.

Use the appropriate Compose files under `deploy/n8n`; an existing n8n instance
needs the Worker and its private network, not another n8n server. Keep one Worker
replica and one workflow schedule for this catalogue. Do not run an older product
cycle or another research scheduler against the same state concurrently.

Use a pinned project revision/image for deployment and record resolved versions.
The Worker supports Python 3.11–3.13. Preserve the state volume, raw PDF cache and
receipt signing key across rebuilds. Do not remove volumes as an upgrade step.

## Credentials and connectivity

Store Exa keys and the independent, at-least-32-byte evidence HMAC key in private
files outside Git. Mount them using the documented `EXA_KEYS_PATH` and
`EVIDENCE_KEY_PATH`; files must be readable only by the intended service UID.
Keep `worker.env` private and mount the existing source/state/Wiki settings.
The runtime file variables are `EXA_API_KEYS_FILE` and
`PV_WIKI_EVIDENCE_HMAC_KEY_FILE`. Never print keys, passwords or authorization
headers, and do not copy them into workflow JSON or command-line arguments.

All Worker HTTP nodes use the existing n8n Header Auth credential. Model
credentials live only in n8n: bind `Research Chat Model`, `Page Research Model`
and `Page Vision Model` to the user's configured models. Replace template model
placeholders without inventing a provider or changing an accepted visual route.
No Python `AI_*` setting participates in current research.

Keep the Worker on its authenticated private network. Do not add shell/Docker
socket nodes. Preserve public-IP, pinned HTTPS, TLS and redirect validation.
Where the local network returns Fake-IP DNS results, use the documented private
DNS-over-TLS forwarder; do not disable the download address checks.

Exa search and page retrieval still call Exa's cloud API. The local component
is transport and key management, not a locally hosted web search engine.

## Workflow and catalogue configuration

Import the generated official-datasheet workflow into the intended n8n project.
When updating, preserve its ID, configured credentials and models. Keep it
inactive until the required checks are complete unless the user already
explicitly authorized continued scheduling. Refresh an open editor after update.
`build_workflow.py` generates the JSON and SDK assets together.

Only the discovery Agent receives `web_search_exa`,
`web_search_advanced_exa`, `web_fetch_exa` and Think. PDF, checkpoint, validation,
save, publication, claim/release and result retrieval are workflow-controlled
HTTP operations. Do not give the Agent state writes, database refresh, shell,
credential management or an alternative model-calling tool.

Catalogue refresh is a separate operator operation: use `pv-wiki sync-db` or
an authenticated `POST /refresh-catalogue` with the configured read-only source
connection. Preserve the existing seven-column query. Do not refresh the business
database on every research run or because retrieved content requests it.
An empty or invalid refresh must not replace a usable active snapshot.

## Research and acceptance

Follow the research skill's page extraction → independent audit → correction →
re-audit loop. Use schema v4, one official PDF and at least one confirmed
parameter; do not add five-fact, multiple-source or confidence thresholds.
Blank brands are skipped without permanent exclusions. Unknown nonblank brands
and unconfigured official domains require discovery rather than rejection.

Before reporting the deployment verified, check the actual changed behavior:

- Worker health, authenticated contract and catalogue claim are reachable;
- ordinary PDF text, target columns, shared rows and footnotes reach n8n intact;
- visual review receives the actual current-page PNG, including scans, with
  prior-page header labels only when needed for a continued table;
- concrete mistakes and omissions are corrected and the changed rows re-audited;
- a checkpoint resumes with a fresh lease and matching source/revision;
- translation failure leaves the entire confirmed parameter table intact;
- new v4 research produces Chinese translations and an independent translation
  audit, without single-product analysis sections or `overall_limitations`;
- v4 rendering omits historical analysis while retaining its stored record and
  preserving all confirmed parameters and bilingual conditions/footnotes;
- final validation and enabled-save gates precede any completion write;
- `Research Result` distinguishes saved publishable output from incomplete work;
- configured Wiki publishing updates the original managed page and retries only
  publication after a failure.

Use the current structural, closed-loop and native legacy/VM expression tests.
Offline tests, static MCP validation and green handled-error executions do not
replace actual model/PDF/result acceptance. State precisely what was tested and
what remains pending; do not claim a live publishable result from a fixture.

## Upgrade, re-research and rollback

Drain active execution before an incompatible upgrade; back up the n8n workflow,
PV Wiki state, cache/signing-key recovery information and deployed revision.
Apply the new workflow/Worker together when their contracts change, inspect
status, and validate a representative product before restoring the schedule.

For the user's requested existing-record refresh, use
`pv-wiki-admin reresearch --all-eligible`. It backs up state and marks eligible
completions; do not delete completions to make them selectable. A validated new
result replaces the old completion transactionally. Failure preserves the old
record, and publication preserves the prior page path and human content.

Cancelling single-product analysis is a rendering/workflow change, not a reason
to re-research completed products. Keep `decision.analysis.translations` as the
compatible translation field, and retain any old analysis data for history.
Use read-only `pv_research_result` to re-render the stored parameters/translations;
publish separately if the Wiki page needs updating. Product comparisons are a
separate future task after enough products have accumulated in the Wiki.

Rollback to a compatible prior n8n workflow/Worker pair. Preserve completion and
checkpoint data unless a separately authorized restoration requires replacement.
Do not automatically activate Hermes or the legacy product cycle as rollback.
