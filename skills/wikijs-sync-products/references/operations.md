# n8n PV Wiki operations

Use this runbook for the current official-datasheet workflow. Research decisions
and every model call run in n8n; the Python Worker provides Exa transport, PDF
processing, validation, durable state and Wiki.js writing. See the
[current deployment guide](../../../deploy/n8n/README.md) and
[local Worker guide](../../../deploy/n8n/LOCAL-WORKER.md) for Compose commands,
credential mounts, model bindings and the documented DNS forwarder.

## Runtime ownership and boundaries

Run one Worker replica and one active research workflow for the catalogue.
The Worker provides an atomic claim and expiring execution lease; manual and
scheduled executions cannot research the same Worker task concurrently.
Tool requests renew a valid lease. A cancelled process cannot reuse an expired
lease; a later execution obtains a fresh claim. Do not run old `/run-one`
workflows, another research scheduler or a parallel state-changing CLI operation
against the same state and Wiki prefix.

The discovery Agent has only three Exa tools and Think. Claims, PDF processing,
checkpoint, validation, save, result retrieval and publication are ordinary
HTTP nodes under workflow control. Do not attach catalogue refresh, database
credentials, shell or state-writing tools to the Agent.

Keep the Worker on an authenticated private network. Bind all Worker HTTP nodes
to the existing n8n Header Auth credential. Keep model credentials in n8n and
reuse the configured research/visual routes; the current Worker uses no `AI_*`
model settings. Exa requests still use Exa's cloud service.

## Catalogue refresh

The current deployment may keep its authorized SELECT-only source connection in
private Worker configuration. Refresh is an operator action using `pv-wiki
sync-db` or authenticated `POST /refresh-catalogue`; it is not a per-product
research step or a model tool. Use the accepted source endpoint and explicit
TLS settings. Never expose connection secrets in prompts or result/log output.

The source query retains these seven columns from `public.products`:
`product_id`, `brand_code`, `family_code`, `product_name`, `unit_of_measure`,
`created_at`, `updated_at`. The scan uses a read-only repeatable-read transaction.
The state update activates a complete catalogue snapshot atomically; invalid,
empty or duplicate-ID input must not replace a usable snapshot.

Blank/null/whitespace brands are ineligible without a search or permanent
non-publish conclusion. Adding a brand permits later selection. Unknown nonblank
codes and old category mappings remain discoverable; do not treat them as proof
that the product is a fastener. Missing source products leave the active snapshot
without deleting accepted completion records or existing Wiki pages.

## Research execution and self-review

Each execution claims one pending publication, compatible saved checkpoint or
eligible unfinished product. It then follows the n8n page extraction and
independent review loop described in the [research skill](../../pv-wiki-research/SKILL.md):

1. Discover the manufacturer, exact model and official PDF; download the original.
2. Locate target/shared/continued pages from complete ordinary page text.
3. Extract page parameters, then audit every row and missing item independently.
   Visual audits receive the actual current-page PNG and can request crops.
   Continued tables may use prior ordered header labels without other-page values/images.
4. Correct/add rows and independently review those changes again. Preserve
   original labels, units, conditions, model scope and footnotes; explain omissions.
5. Merge confirmed rows. Translate names, text values, conditions and footnotes
   into Chinese in batches, then independently audit those translations without
   cutting rows. Do not generate single-product analysis or `overall_limitations`.
6. Inject the claimed identity/revision and validate the schema-v4 result.
   Deterministic validation errors return to the affected page for correction.
7. Save only a validated, nonempty result when saving is enabled. Publish only
   after a successful completion save and when Wiki.js is configured.
8. Release the session and emit the final result node.

One official PDF and one confirmed parameter suffice. No v4 five-fact,
multiple-source or confidence threshold is applied. Search/fetch/authentication,
lease and provider connection failures end the current attempt; they are not
repairable evidence errors and do not justify blind paid-request replay.
Translation failure leaves confirmed rows intact. Text can be reviewed
when images are unavailable, but scans cannot become confirmed text by guessing.

## Checkpoint and result recovery

`Checkpoint Progress` writes `pdf_ready`, `draft` or `reviewed` state to
`product_research_checkpoints`, separately from completions. It preserves PDF
references, page text, page plan, candidate rows and concise review history.
The bounded JSON excludes credentials, lease tokens and embedded image data.
A checkpoint is untrusted recovery input, not publication authority.

A later claim resumes only a matching catalogue source hash and current
research revision; n8n also checks its workflow contract. It acquires a new lease
and continues from the last stored stage, including re-auditing changed rows.
Final validation still precedes save. Successful saving clears that matching
checkpoint. Retain the state volume, PDF cache and signing key during restart.
An interrupted step not yet checkpointed may need to run again; do not claim
at-most-once model billing from checkpoint support.

Inspect n8n **Executions → execution → Research Result → Output / JSON**.
The final node follows `Release Research Session` and reports separate `saved`,
`published` and `session_released` fields. A green handled-error execution alone
is not a saved product. Outcomes are:

| Outcome | Meaning |
| --- | --- |
| `published` | Saved and published; inspect `wiki_path` |
| `publishable` | Saved accepted result; inspect `markdown` and `parameters` |
| `reviewed_preview` | Validated this run with saving disabled |
| `incomplete` | Inspect `error`, `omissions` and `review_summary`; no new completion |
| `no_work` | No task was claimed; inspect `No Work Result` |

Use authenticated `POST /datasheet/tools/pv_research_result` with
`{"product_id":"PRODUCT_ID"}` to retrieve an existing result or checkpoint
summary without a research lease. The API is read-only and still needs Worker
Header Auth. Do not manually promote a checkpoint or raw execution draft.

The current v4 renderer omits product analysis. Historical analysis stays in the
stored record, and `decision.analysis.translations` remains the compatibility
field for bilingual display. Re-render existing parameters and translations with
`pv_research_result`; this display change needs no new discovery, model calls or
re-research marks. The read-only endpoint does not update an existing Wiki page;
apply the new display through a later publication update. Comparative analysis
is deferred to a separate task after the Wiki contains enough products.

## Completion replacement and publication retry

Accepted research is saved before any Wiki mutation. Without Wiki.js settings,
keep it as a saved publishable version. A publication failure leaves a pending
completion; the next eligible publication attempt updates the same managed
page and does not redo search/model research. Preserve the prior Wiki path and
human-authored region. Fix connectivity, permissions or content problems
rather than deleting a completion to retry publication.

Publication-only recovery is also available through:

```sh
pv-wiki-publish-researched
pv-wiki-publish-researched --product-id PRODUCT_ID
```

For user-requested replacement of existing completed records, use:

```sh
pv-wiki-admin reresearch --all-eligible
```

The command backs up state and marks existing eligible completions, including
old published/non-published and historical backfill records. Ordinary startup
does not repeat these marks. Marked old pending results leave the ordinary
publication queue. Only a newly validated result replaces the old completion,
clears its re-research mark and resets publication state; failure retains the
old record. Blank-brand records and their Wiki pages are not automatically
deleted. Do not run this management operation during an active research claim.

Schemas v2/v3 and historical completion backfill remain readable/renderable.
Keep their records during upgrades; do not reinterpret an old receipt as v4
provenance or silently convert old decisions to the new schema.

## Exa keys and signing-key maintenance

Store authorized Exa keys in the private file named by `EXA_API_KEYS_FILE`.
Commas or whitespace may separate keys; maintain mode `0600` and ownership for
the configured service UID. Keep the evidence HMAC secret separate, at least
32 random bytes, at `PV_WIKI_EVIDENCE_HMAC_KEY_FILE`. Do not put raw keys in
workflow JSON, model prompts, URLs, tool inputs or general-purpose logs.

The key pool's definitive-response behavior remains:

| Exa response | Pool behavior |
| --- | --- |
| `401` / `API_KEY_BUDGET_EXCEEDED` | Disable that key and try another healthy key |
| `NO_MORE_CREDITS` / `TEAM_BUDGET_EXCEEDED` | Stop for the account/team without sweeping keys |
| `429` | Honor cooldown/Retry-After and select a healthy key where available |
| Other `4xx`, `5xx`, malformed response or network failure | Return failure; no cross-key replay |

Key rotation provides availability, not additional account quota. Rotate the
key file atomically, retaining permissions; drain active work before restarting
the service that owns the pool. A process restart resets its pool cursor and
cooldowns, not completed research. Preserve the evidence signing key unless
performing a planned rotation: older receipts cannot verify against a new key.
Do not delete state or repeatedly rerun paid tools as a credential test.

## Monitoring, upgrades and rollback

Use authenticated Worker health/status/result endpoints, n8n execution history
and the final result fields. Alert on meaningful systemic failures such as an
unavailable state store, rejected catalogue refresh, unavailable provider keys
or repeated Wiki failures. Incomplete product evidence is not a permanent
negative completion or a request for a manual ticket on every run. Redact
credentials and unnecessary source payloads before sharing logs.

Before upgrading, drain execution and back up the PV Wiki state (including
catalogue, completions, re-research marks and checkpoints), n8n workflow/config,
PDF cache recovery information and deployed revision. Back up Wiki.js through
its own procedure. Protect secret backups separately. Restoring older state
may deliberately forget newer progress; compare counts after recovery.

Install a compatible Worker/workflow pair, preserve credentials and model IDs,
run the relevant offline checks, then verify a real product through n8n. Static
MCP validation and offline fixtures do not prove real visual/model acceptance.
Reactivate a schedule within the user's authorized scope only after required
checks. Do not automatically rewrite pages or remove state on upgrade.

Rollback to a known-compatible n8n workflow and Worker revision. Keep one
scheduler active and preserve data unless an explicitly intended restoration
requires replacement. Neither Hermes nor the older `/run-one` product cycle
is an automatic fallback for this deployment.
