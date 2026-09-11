# Implementation and validation record

Source snapshot: `codex/n8n-datasheet-research` from the original `sulirang/pv-wiki`
checkout, based on `fdf963091a69cac8a59af3e5fc30e9e14157f1cd`. This independent
repository starts from that working-tree snapshot on its `main` branch.

## Translation-only enrichment — 2026-09-11

The user deferred product analysis until enough Wiki products are available for
a separate comparison task. The current workflow therefore retains parameter
extraction, visual/independent review, Chinese translation and independent
translation audit, but no longer generates single-product analysis sections or
`overall_limitations`. The translation stage's renamed nodes are:

- `Prepare Analysis Batches` → `Prepare Translation Batches`
- `Optional Analysis and Translation` → `Translate Parameters`
- `Merge Optional Analysis` → `Merge Translations`
- `Audit Optional Analysis` → `Audit Translations`
- `Read Analysis Audit` → `Read Translation Audit`
- `Retry Without Analysis` → `Retry Without Translations`

`decision.analysis.translations` remains a storage-compatibility field; original
parameters, conditions, footnotes and their provenance remain unchanged.
The renderer exposes `translation_status`/`translation_omissions` without
reporting historical analysis as a current failure. The compatible contract's
`analysis_example` contains translations only, `analysis_section_codes` is empty
and `product_analysis_enabled` is false.

Current v4 rendering omits analysis from both new and historical results.
Historical analysis remains stored, and existing parameters/translations can be
re-rendered without re-searching, re-reading a PDF or making new model calls.
Read-only result retrieval does not publish a Wiki update. If a historical
introduction contains the Unicode replacement character U+FFFD, rendering uses
the known manufacturer and model as a neutral introduction; the database record
is not changed to conceal or replace the damaged historical text.

The following targeted checks passed for this update:

- **86 backend tests** across `test_datasheet_decision.py`, `test_n8n_api.py` and
  `*render*.py`, covering translation-only decisions, the compatible contract,
  nonfatal translation degradation and old-record rendering.
- **11 Python workflow tests** and all **48 review-core cases**.
- The workflow smoke test retained **350 rows** through translation, independent
  audit and failure degradation; the page-context smoke test also passed.
- Actual **n8n 2.37.10 legacy/VM expression checks** passed, including model-input
  isolation. SDK validation passed without warnings for the 123,635-character
  generated SDK.

The existing 88-node n8n workflow has been updated and remains inactive. Final
readback matched every node parameter against the correctly decoded UTF-8
template, including all six renamed nodes. Existing model selections,
credentials, Configuration and topology were preserved. Its pre-update backup
is `/etc/pv-wiki/workflow-backups/before-translation-only-20260911T065412Z.json`.
The final Worker image was rebuilt and restarted successfully; `/healthz`
returned HTTP 200. Authenticated read-only result retrieval verified the deployed
renderer against the existing saved product.

The existing product retains all **47 original parameters and their Chinese
translations**. Historical analysis remains in the database, while the current
v4 rendered page omits it. The current rendered artifact is
`../artifacts/translation-only/HUAWEI-SUN2000L-3.68KTL.md`; the current Markdown
in `../artifacts/bilingual-live/` has been synchronized to the same display.
Execution 18's original JSON remains unchanged as historical evidence.

The read-only export's parameter array exactly equals execution 18's saved
`result.parameters`. All parameter tables match the earlier rendered output
verbatim, preserving Chinese translations, conditions and footnotes. The page
contains no product-analysis sections, spurious analysis-failure messages or
U+FFFD replacement characters. Only the damaged introduction's display falls
back to the known `Huawei SUN2000L-3.68KTL` identity. The stored decision SHA-256
remains `a01eb1d774fe93d6b6a9cecf86a5da905e09cd956ece72af3b7e706c4aeba74e`, and
`completed_at` is unchanged. All checks in
`../artifacts/translation-only/render-checks.json` passed.
Final acceptance is recorded in `../artifacts/translation-only/acceptance.json`.
Both current Markdown files match the deployed Worker's readback verbatim,
apart from the terminal newline in the files.

This update started no model, Exa or PDF research and made no Wiki publication.
It did not repeat the full Python 3.11/3.12/3.13 matrix or perform a new end-to-end
model research run. The dated records below describe earlier versions; their
matrix and online acceptance results are not this update's test results.

## English-source preference and bilingual enrichment — 2026-09-08

The 88-node workflow now asks discovery to search for a matching official English
datasheet first. Exact model, generation, market and document-version applicability
remain higher priorities than language. Non-English fallback records
`official_source.datasheet_language` and `language_selection_reason`; a language
path or filename alone is not proof, and changing URL suffixes is not discovery.

Optional enrichment covers Chinese parameter names and groups plus `value_zh`,
`conditions_zh` and aligned `footnotes_zh`. Original source values, conditions and
footnotes remain unchanged. The independent analysis audit checks polarity,
leading/lagging direction, numbers, units, identifiers, exceptions and complete
footnote meaning. It can omit an uncertain field while retaining other supported
translations and the confirmed parameters. An uncertain or malformed note keeps
an empty string in its original position while other valid note translations
remain available. Only a mismatched array length drops the whole footnote field;
a nonempty source string counts as one original footnote, matching the backend.
An explicit model error rejects an accompanying approved-looking audit response.

Offline checks for this update passed: ten Python workflow tests, all 48 review
core cases, the closed-loop smoke test with all 350 rows carrying descriptive
values, full conditions and ordered footnotes through merge/audit, and the actual
n8n 2.37.10 legacy/VM evaluators in a no-network container. The native check also
verifies the wider bilingual audit payload excludes transport secrets and that
model failure still retains all 63 source rows. These checks do not by themselves
claim a new live English discovery or model-translation run.

Follow-up review fixed the two contract edge cases above. The same targeted
workflow tests, 350-row smoke and native legacy/VM checks cover blank/malformed
note positions, single-string source notes, and explicit errors accompanying
otherwise valid approval JSON. The final frozen source snapshot, including the
mixed-unit rendering and page-evidence isolation changes, passed the full WSL2
matrix in no-network containers on **Python 3.11.16, 3.12.14 and 3.13.15**:
each version passed **487 runtime tests + 58 base skill tests = 545**, for
**1,635 passing test runs**. Logs and exit-code records are outside Git at
`../artifacts/test-results-bilingual-accepted-311`,
`../artifacts/test-results-bilingual-accepted-312` and
`../artifacts/test-results-bilingual-accepted-313`. Each `result.json` records
`network:none` and zero exit codes for both groups. The earlier 486 + 58 snapshot
remains in `test-results-bilingual-final-{311,312,313}`; 25 targeted renderer
checks also passed before the final full matrix. Offline success does not prove
completion of the new English/bilingual live research run.
The three Exa tools also now expose distinct model-visible descriptions with
typed JSON inputs and an explicit `urls: ["https://…"]` fetch example. Discovery
may repair `invalid_tool_arguments` responses, which occur before a paid request;
actual search/fetch runtime failures still end the current attempt.
Optional `Configuration.datasheet_hints` defaults to an empty object and maps a
catalogue product ID to URL leads. Initialization selects only the current
product's URLs and refreshes them on checkpoint recovery. Discovery fetches and
verifies these leads before a broad search; they do not bypass official-source,
model, region/version or language checks. Tests check cross-product isolation and
the model-input whitelist. The distributable graph contains no product-specific
hint URL.
Discovery normalizes only a known manufacturer/brand prefix followed by a clear
separator and whitespace around ASCII hyphens; it preserves model suffixes and
the catalogue title. A checkpoint whose discovered model changes under this
normalization, or whose PDF/model-bound receipt identifies another model, starts
discovery and PDF reading again. It retains the selected catalogue revision and
does not mutate the old checkpoint or the original PDF content cache. Compatible
checkpoints continue normally. Targeted smoke/native checks cover the actual
`HUAWEI SUN2000L - 3.68KTL` case, exact column confirmation after normalization,
unknown/near-prefix preservation, suffixes, and both PDF/receipt mismatch paths.
Planning, extraction and audit prompts distinguish a series-cover maximum or
"up to" headline from a target-column value or an explicitly constant shared
specification. Cover marketing numbers cannot substitute for the exact model's
technical table, and curves/model-name digits do not establish specifications.
Explicitly constant parameters and clearly shared non-numeric features remain
eligible. Generated-prompt assertions cover both text and image execution paths.

The successful bilingual acceptance followed three failed attempts. Execution 15 exposed discovery
tool-argument handling, followed by the argument compatibility and repair-routing
fixes described above. Execution 16 stopped safely on a manufacturer-prefix model
binding error; the exact-model normalization and incompatible-checkpoint fixes
address that failure. Execution 17 exposed a separate page-context issue: while
processing page 1 with page 2 supplied as the header, parameter rows from page 2
were incorrectly attributed to page 1. The session was released and the result
reported `outcome:incomplete`, `saved:false`. At that point, the prior 45-parameter
completion and its pending re-research marker remained intact. No successful new
English/bilingual completion or Wiki publication is claimed by executions 15–17.

The page-input correction has been deployed by updating 39 node parameter sets.
Live readback matched the generated changes, returned no warnings and preserved
all 88 nodes, the inactive state, credentials, connections and model configuration.
Execution 18 tested this version with real model calls. It automatically
reset execution 17's incompatible draft and supplied only the current PNG to
`Page Vision Model`; the recorded model input contains one `[image]` marker.
Its initial page-1 extraction returned zero parameters. Independent audit then
proposed three shared non-numeric features, without importing the cover's
98.6% maximum-efficiency headline as a target-model value. The three proposed
cover features did not pass their final scope checks and were omitted. Page 2
supplied all **47 technical parameters**, which independent audit confirmed for
the exact `SUN2000L-3.68KTL` model with page-2 provenance and complete coverage.
The target-column values, original mixed metric/imperial units, conditions and
applicable footnotes 1 and 6 match the official English EU PDF, version
`02-(20190512)`. The raw parameter array remained identical through enrichment
and saving.

**Execution 18 completed successfully at `2026-09-08T08:34:55.324Z`.** Its final
`Research Result` reported `outcome:publishable`, `saved:true`, `published:false`
and 47 parameters. `Read Analysis Audit` retained 47 parameter translations.
Text values display Chinese while retaining the source, including `Yes` as
"支持" and the original power-factor phrase alongside its Chinese leading/lagging
translation. Both source conditions and all four parameter rows carrying
footnote 1 or 6 have separate Chinese paragraphs after the original text.
Optional analysis may omit individual sections; this acceptance does not claim
that every proposed analysis paragraph was rendered.

Independent state readback found one completion containing 47 parameters with
source language `en`, completed at `2026-09-08T08:34:54.261918Z`. Its re-research
status is `replaced`, with zero pending re-research markers and zero checkpoints.
The prior completion was replaced rather than duplicated. Authenticated read-only
`pv_research_result` returned Markdown identical to execution 18's final output.
Artifacts are outside Git under `../artifacts/bilingual-live/`:
`HUAWEI-SUN2000L-3.68KTL.md`, `execution-18-publishable.json` and
`worker-stored-result.json`. Wiki.js is still not configured, so the saved
bilingual result has not been published to Wiki and publication remains untested.

## Current page-review loop — 2026-09-08

The current evidence packet isolates PDF pages. `header_page` cannot point to a
future page. Extraction and audit render and attach only the current page image;
an earlier table contributes model labels, never its complete text, image or
parameter values. Source model-header sequences retain their column order, and
earlier confirmed locators are recorded separately so one confirmed target label
does not replace a full sequence. Uncertain scanned column order is omitted.
The review core may use these labels to identify a continued column, while text
checks for parameter labels and values remain confined to the current page.

Old cross-page drafts/reviews without the current packet marker, including
forward-header contexts, are selectively reset before a completed page can be
promoted. Their derived decisions and affected render receipts are cleared; a
correctly bound original PDF and unaffected page reviews remain available.
On a read-only copy of the real execution 17 checkpoint, initialization and page
selection changed page 1/header 2/47 rows into page 1/header 1/0 rows, preserved
the complete PDF and model-bound receipt, and left the original snapshot unchanged.
`page-context-smoke-test.cjs` covers this shape plus backward legacy packets,
current-image identity, scanned/continued headers and the validation-repair path.

The current generated workflow contains **88 nodes**. Search decisions, page
selection, extraction, independent review, corrections and every model request
run in n8n. Python provides the existing Exa transport, PDF processing, bounded
state/provenance validation, persistence and Wiki.js publication; it makes no
research-model calls. The 52-node import and execution audits below are historical
records, not validation of the new graph.

The replacement addresses failures exposed by executions 7 and 8:

- PDF extraction keeps original layout for deterministic column parsing, but
  provides page-labelled ordinary text to models. `page_texts` contains each
  page's `plain_text`, compact `layout_text`, parse status and independent
  truncation flags. On the real 10-page FusionHome PDF, combined text is 13,325
  characters without truncation. Page 4 retains the 350–450 Vdc battery range,
  battery suffixes, derating conditions and all page-bottom footnotes.
- Extraction and audit are separate n8n model nodes. The audit receives the actual
  current `page` PNG binary message plus its own source text and candidate rows.
  Earlier header context contains only model labels and explicit provenance.
  It checks every row, supplies corrections/additions, and sends changed rows
  through another review. Unsupported rows are omitted with reasons. A local
  row ledger prevents a candidate's own `confirmed` label from authorizing it.
- Model prompts contain only discovery hints, the requested page or final
  parameter batches. Transport envelopes, PDF receipts and leases remain in
  workflow state. The discovery Agent has only three Exa tools and Think;
  checkpoint, validation, save and publication are ordinary HTTP nodes.
- `Build Decision` injects the claimed product and revision. Validation and save
  expressions read their current workflow item, eliminating the prior AI revision
  and negative-run-index failure modes. Saving requires both successful validation
  and the explicit Save Enabled gate. Analysis errors retry parameters alone.
- `Checkpoint Progress` stores PDF-ready, draft and reviewed state separately
  from completed research. A later claim can resume only compatible catalogue
  source/revision state, with a fresh lease. Checkpoints are untrusted recovery
  inputs and still require final validation; they are cleared after successful
  save. Secret fields, lease tokens and embedded image data are rejected.
- After release, `Research Result` reports `published`, `publishable`,
  `reviewed_preview` or `incomplete`, with parameters, omissions, review summary
  and saved Markdown where available. `No Work Result` handles an unacquired
  claim. Authenticated `pv_research_result` retrieves saved output without a lease.

Completed checks for this change:

- Full WSL2 runs on **Python 3.11.16, 3.12.14 and 3.13.15** each passed
  **466 runtime tests + 58 base skill tests = 524**, for **1,572 passing test
  runs** across the three interpreters. Both groups exited successfully on each
  version. Logs are outside Git at `../artifacts/test-results-closed-loop-311`,
  `../artifacts/test-results-closed-loop-312` and
  `../artifacts/test-results-closed-loop-313`. Each directory contains
  `runtime.log`, `skills.log` and `result.json` with the interpreter and exit codes.
- WSL2 Python 3.12, no-network container: 37 document/datasheet tests passed,
  including generated sparse/multi-model/continued tables, footnotes, budget
  exhaustion and image-only PDF rendering. The real FusionHome read-only parse
  used the cached original PDF and did not save a research completion.
- Seven updated workflow structure tests passed, including independent image
  audit, checkpoint/review loops, restricted Agent tools and save-path gates.
- The final review-core smoke test passed **48 cases**. The closed-loop workflow smoke
  also passed, covering correction/recheck, completed-page recovery, full
  350-row retention, optional-analysis degradation and truthful result states.
- The installed n8n 2.37.10 image passed `from-ai-smoke-test.cjs` in a no-network
  container with both legacy and VM evaluators. It checks all three Exa tool
  payloads, current-item checkpoint/validation/save, narrow prompts with private
  transport sentinels, explicit Boolean save gates and failure-safe retention
  of 63 parameters across analysis batches. The final legacy/VM run also passed.
  No provider or Wiki calls were made by these offline checks.

Relevant local checks are `closed-loop-review-smoke-test.cjs`,
`closed-loop-workflow-smoke-test.cjs`, `from-ai-smoke-test.cjs` and
`tests.test_n8n_datasheet_workflow`. `workflow-smoke-test.cjs` is now a compatibility
wrapper that runs the new closed-loop workflow smoke test. The earlier 436-test
runtime matrix below is historical; the 466 + 58 matrix above records the
page-review-loop snapshot, and the final 487 + 58 bilingual snapshot is recorded in the
preceding section. Later live-driven changes still require their relevant regression
checks; the count alone does not certify every subsequent patch.

**Live error-correction and checkpoint-recovery acceptance now passes through
durable saving of a publishable result.** Execution 12 completed independent
re-review, optional-analysis review, server validation and saving; executions 13
and 14 read the result back without researching or saving again. Execution 14
also verified the corrected Markdown rendering. This acceptance was
seeded with execution 8's incorrect 44-row draft and its official PDF, so it
tests error injection and checkpoint recovery, not a fresh search-to-result run.
Wiki.js is not configured: actual Wiki publication has not been tested. Markdown
display cleanup left the confirmed and saved parameter array unchanged.

### Live executions 9–14: correction, recovery, saving and rendering verified

The live fixture used product `01073802`, `SUN2000L-3.68KTL`, the original
official FusionHome PDF and the existing incorrect 44-row candidate from
execution 8. The wrong values were not manually repaired before the test.
n8n's models received the real source pages and performed the corrections.

Execution 9 exposed a model-output limit: the 12,000-token response was truncated
after the first 11 row checks, so it could not provide the complete structured
audit. The workflow was changed to repair malformed/unfinished structured output
automatically and the structured model output budget was raised to 32,768 tokens.
Execution 10 used the older 12,000-token workflow and failed on the same limit;
it is not a test of the updated budget.

Execution 11's first real model audit produced substantive source corrections
without a manual parameter-editing step:

- battery voltage `350~450 Vdc` and the exact `RESU 7H_R / 10H_R` compatibility
  suffixes;
- both power-factor endpoints at `0.8`, the `G83/2` standard and the complete
  battery-related condition for the `495 V` input limit;
- removal of two unsupported DC entries and an unsupported EMC entry;
- four proposed additions covering single-phase connection, THD and AC protection.

The initial local check was overly strict about a series header combining model
suffixes. The general recognition fix preserves exact target/sibling distinction.
In its second audit round, execution 11 recovered a 45-row candidate by itself.
A connection error stopped the third model request; the workflow retained the
round-2, revision-2 checkpoint with all 45 rows and correctly returned incomplete,
not saved. The artifact is
`../artifacts/self-review-live/execution-11-intermediate.json`.

**Execution 12 resumed that checkpoint and independently confirmed 45/45 rows in
round 3, with complete coverage.** The review history's 42 intermediate error
descriptions were reduced to the three unsupported omissions: two DC
entries and EMC. Resolved intermediate failures are not presented as permanent
missing parameters. The real confirmed parameter artifact is
`../artifacts/self-review-live/execution-12-confirmed-parameters.json`; its recorded
stage is `parameters_reviewed` and `saved:false` records that earlier checkpoint,
not execution 12's final outcome.

Execution 12 finished successfully from `2026-09-08T06:34:31.950Z` to
`2026-09-08T06:40:20.290Z` (5 minutes 48 seconds). After confirming all 45
parameters, it produced 45 Chinese parameter translations. Independent analysis
review reduced five proposed analysis sections to four sections; this counts
section categories, not paragraphs. The actual final responses were:

| Step | Verified response |
| --- | --- |
| `pv_validate_research` | `ok:true`, `valid:true`, `parameter_count:45`, `errors:[]` |
| `Save Research` | `ok:true`, `created:true` |
| `Read Research Result` | `completed:true`, `publishable:true`, `reresearch_pending:false`, `checkpoint:null` |
| `Research Result` | `outcome:publishable`, `saved:true`, `published:false`, `session_released:true` |

Execution 13 then completed in about 0.5 seconds using the pending-publication,
result-readback and release path. It returned the same 45 saved parameters and
ran no model, search or Save Research node. With Wiki.js unconfigured it did not
publish. This verifies that the accepted result survives a new execution without
repeating research or creating another completion.

Final artifacts are outside Git at
`../artifacts/self-review-live/execution-12-publishable.json` and
`../artifacts/self-review-live/HUAWEI-SUN2000L-3.68KTL.md`. The execution JSON
preserves the real final response, including its original rendering.

The renderer fix for duplicated units and repeated section headings is now
implemented. **65 related renderer, storage and checkpoint tests passed.** An
offline render of the real saved 45-parameter decision retained all 45 parameters
and left the decision unchanged; only its displayed Markdown was corrected.
The Worker was rebuilt with this renderer and its health check passed.

**Execution 14's live readback passed all ten acceptance checks.** It succeeded
from `2026-09-08T06:51:06.805Z` to `2026-09-08T06:51:07.291Z` (0.486 seconds),
using only the claim, pending-publication, result-readback and release path.
It ran no model, search, PDF-processing or Save Research node. Its 45-parameter
array was identical to execution 12's saved array under `JSON.stringify`.
The final response reported `outcome:publishable`, `saved:true`,
`published:false`, `publication_configured:false` and `session_released:true`.

The actual returned Markdown contained no duplicated units, contained exactly
one Input group and one Uitgang group, and used the exact title
`HUAWEI SUN2000L - 3.68KTL`. That real output was exported again to
`../artifacts/self-review-live/HUAWEI-SUN2000L-3.68KTL.md`.
`../artifacts/self-review-live/execution-14-rendered.json` retains the actual
result plus all ten successful acceptance checks. Keep
`execution-12-publishable.json` unchanged as historical evidence of the original
save and rendering. This completes saved-result and rendering acceptance;
actual Wiki publication and a fresh-discovery run remain outside the scope of
this checkpoint-based test.

The configured n8n MCP created an inactive, unpublished workflow on 2026-09-07:

- Name: PV Wiki - Official Datasheet Agent
- ID: `5JP6B0OgKH59sGmW`
- Project: Cybrex Kobabab's personal project; project root (no folder)
- Link: http://localhost:5678/workflow/5JP6B0OgKH59sGmW
- 52 nodes; no credentials assigned; timezone Asia/Shanghai

The SDK source passes the official MCP workflow validator, and all node
configurations passed validation. Remaining PLAIN_GENERIC_AUTH notices concern
the unbound worker authentication: bind an existing Header Auth credential,
or use the template-auth setup described in the deployment guide. After SDK
creation, set the workflow timezone to Asia/Shanghai; JSON import already carries it.

## Automated checks

WSL2 Debian with Docker, isolated source copy and official Python images:

| Python | Runtime suite | Base skill suite |
| --- | --- | --- |
| 3.11.16 | 436 passed | 58 passed |
| 3.12.14 | 436 passed | 58 passed |
| 3.13.15 | 436 passed | 58 passed |

After that matrix, the final PDF-review coverage refinement was verified with
all 19 schema-v4 tests on each of the three Python versions. It marks partial
page review accurately without blocking confirmed parameters. The updated
n8n workflow also passed its 6 structural tests and Node data-flow smoke tests,
including invalid review pages, actual binary image payload construction,
350 parameters, optional-analysis degradation, and repair/search/view loops.

The PDF tests include real generated image-only PDFs rendered to full and
cropped PNGs, and receipt/cache verification. State tests cover one-parameter
publication, blank-brand selection, replacement transactions, concurrent
revision checks, publication retry, original page paths and human content.

Full matrix logs are outside the Git repository at
`../test-results/n8n-v4-final-3.11`, `3.12`, and `3.13` (same prefix).
No live provider or Wiki.js requests were made by these tests.

## Initial deployment checks (historical)

Bind the worker URL/authentication and n8n model credentials. Run one official
datasheet through the actual n8n instance and verify the provider receives PNG
image messages. Static node validation and mocked data flow are not that test.

The one-time production backup/research replacement command is implemented,
but has not been run against the user's state database. Existing Wiki content
has not been modified. Configure the worker and state/Wiki credentials before
running the backed-up migration and activating recurring execution.

## Exa tool expression fix — 2026-09-08

Live execution 4 failed inside n8n before any Exa HTTP request: all three search/fetch
tools used `$fromAI('arguments', ...)`, and n8n 2.37.10 rejects `arguments` as an
unsafe object property. The placeholder is now `tool_input`; the HTTP JSON body
remains the original query/URLs object. `pv_validate_research` already uses the
valid placeholder `decision`.

The existing workflow `5JP6B0OgKH59sGmW` was backed up and updated in place.
Readback confirmed that only the three Exa `jsonBody` parameters changed; model
settings, credentials, node positions and connections were preserved. The backup
is in WSL at `/etc/pv-wiki/workflow-backups/exa-before-20260908.json`.

Validation: 7 workflow tests and the data-flow smoke test passed.
`from-ai-smoke-test.cjs` also passed inside the running n8n container using its
real expression evaluator and tool schema generator: it reproduces rejection of
the old key and checks all three corrected payloads plus the validation payload,
without network access. Run it with:

```sh
node from-ai-smoke-test.cjs pv-wiki-datasheet-agent.json /usr/local/lib/node_modules/n8n
```

A separate authenticated Worker search returned one real Exa result (USD 0.007),
then released its research session. This verifies search connectivity; it is not
a full model/PDF/Wiki publication run. Reload the n8n editor to use the updated
workflow rather than an already-open draft.

## Validation tool VM expression fix — 2026-09-08

Execution 7 made 26 validation tool calls that failed before reaching Worker.
The n8n 2.37.10 VM expression engine returns `undefined` for the explicit
negative run index in `$('Compose Input').last(0,-1)`. The legacy engine resolves
that reference, which explains why the earlier offline smoke check passed.
The validation tool now uses `.last()`, preserving the latest product and PDF
context in both engines. Code node scripts are unchanged.

The native smoke check now explicitly initializes both legacy and VM engines,
exercises direct and wrapped AI inputs at tool run indexes 0, 3 and 25, and
checks the complete payload against the latest of two Compose Input runs.
Both engines passed inside the running n8n container without HTTP/model calls.
Replay of execution 7's actual decision and PDF inputs also preserved the
product, source hash, PDF content and evidence receipt after the reference fix.

The live workflow was backed up and patched in place. Readback matched the
intended single-node change; all connections, model settings, credentials and
the inactive status were preserved. Backup:
`/etc/pv-wiki/workflow-backups/validation-before-20260908.json`.

## Execution 7 server validation and output audit — 2026-09-08

The source catalogue name `HUAWEI SUN2000L - 3.68KTL` was incorrectly split into
partial model fragments. Internal separator whitespace now preserves the full
model; family-only names, rating-only fragments and adjacent model/suffixes
remain rejected. A signed PDF served directly by an exact official host bound
to the source catalogue brand now remains valid when an attached support page
has no verified download link. This does not trust a model-supplied manufacturer,
an unknown CDN, a lookalike domain or a redirect ending outside that brand's
configured hosts. The page's unverified link status is preserved.

Targeted checks: 36 model-decision tests, 24 datasheet-decision tests, 8 receipt
tests and 35 storage tests passed. The rebuilt local Worker accepted execution
7's existing four-parameter candidate and original signed PDF during read-only
revalidation, without another model call or a save/publication request.

The ten current-page image hashes matched the original PDF renderings and
signed view manifests, and all visual model logs contained three image messages.
The provider's raw HTTP image body was not retained. Visual interpretation was
nevertheless unreliable: page 4 values conflicted with the table and page 5
was an optimizer rather than an inverter. All four retained parameter values
were checked against PDF page 4. The candidate analysis still incorrectly treats
the full-power MPPT range as a startup/whole operating range; server structural
validation does not certify that interpretation. It was not saved or published.

The source PDF, candidate, audit and revalidation summary are outside Git under
`../artifacts/execution-7` and `../artifacts/review-execution7-vision`.
Completed, pending and published records remain zero. Wiki.js connection settings
are still missing, so end-to-end Wiki publication has not been verified.

## Execution 8 revision and failure routing — 2026-09-08

Execution 8 reached the validation tool, but the model supplied the source hash
as `expected_revision` instead of the claim's `new:` revision. The Worker marked
this candidate error as a failed session, rejecting later corrected requests.
The tool now injects the trusted latest claim revision after the AI candidate.
Worker candidate-object/revision errors return repairable validation feedback;
product identity, source hash, lease and real fetch errors retain their existing
session boundaries. A failed revision cannot authorize saving another revision.

Compose also received two explicit `terminated` model errors. Read Candidate
and Read Self Review now stop on explicit runtime errors instead of treating
them as empty candidate JSON. Invalid JSON and the explicit structured-output
format error remain repairable. Optional analysis and visual degradation retain
their existing parameter-preserving behavior.

Checks passed: 18 n8n API tests, 7 workflow tests, the 350-row data-flow smoke,
and native n8n legacy/VM expression checks with missing/wrong/stale AI revisions.
Worker was rebuilt, and the live workflow was backed up before patching only
`pv_validate_research`, `Read Candidate` and `Read Self Review`. Readback preserved
all other node configuration, credentials, connections and inactive status.
Backup: `/etc/pv-wiki/workflow-backups/revision-before-20260908.json`.

The execution's existing candidate was replayed with a fake catalogue and an
isolated in-memory session in the deployed Worker. A wrong revision returned
`valid:false`; the corrected revision passed in the same session, without a save
or external provider call. Live completed/pending/published counts remain zero.

The 44-row intermediate candidate is not publication-ready: PDF comparison found
incorrect battery values, power factor and standard names, unsupported protection
and EMC entries, and lost conditions. It never reached independent Self Review.
Layout whitespace also consumed the extraction budget and truncated later rows
and footnotes; plain extraction from the same PDF preserves the full page in
3,192 characters. That issue remained in the execution-8 revision-only patch;
the current page-review loop described above fixes the parser/context path.
Artifacts are outside Git at `../artifacts/execution-8`.
