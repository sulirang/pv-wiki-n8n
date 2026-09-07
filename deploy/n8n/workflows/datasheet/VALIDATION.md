# Implementation and validation record

Source snapshot: `codex/n8n-datasheet-research` from the original `sulirang/pv-wiki`
checkout, based on `fdf963091a69cac8a59af3e5fc30e9e14157f1cd`. This independent
repository starts from that working-tree snapshot on its `main` branch.

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

## Pending deployment checks

Bind the worker URL/authentication and n8n model credentials. Run one official
datasheet through the actual n8n instance and verify the provider receives PNG
image messages. Static node validation and mocked data flow are not that test.

The one-time production backup/research replacement command is implemented,
but has not been run against the user's state database. Existing Wiki content
has not been modified. Configure the worker and state/Wiki credentials before
running the backed-up migration and activating recurring execution.
