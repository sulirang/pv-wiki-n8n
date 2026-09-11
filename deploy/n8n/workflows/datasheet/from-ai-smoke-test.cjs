'use strict';
// Native n8n legacy + VM expression checks. No credentials or network requests.
// node from-ai-smoke-test.cjs [workflow.json] [n8n installation directory]
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const runtimeRequire = createRequire(path.join(
  process.argv[3] || '/usr/local/lib/node_modules/n8n', 'package.json',
));
const { Workflow, Expression } = runtimeRequire('n8n-workflow');
const { getSchema } = runtimeRequire(path.join(
  path.dirname(runtimeRequire.resolve('n8n-core')),
  'execution-engine/node-execution-context/utils/create-node-as-tool.js',
));
const definition = JSON.parse(fs.readFileSync(
  process.argv[2] || path.join(__dirname, 'pv-wiki-datasheet-agent.json'), 'utf8',
));
const workflow = new Workflow({
  ...definition, id: 'closed-loop-offline-smoke',
  nodeTypes: { getByNameAndVersion: () => undefined },
});
const nodes = Object.fromEntries(definition.nodes.map(node => [node.name, node]));
const copy = value => JSON.parse(JSON.stringify(value));

function resolve(name, expression, input, runIndex = 0, isTool = false) {
  const item = { json: input };
  const runs = [];
  runs[runIndex] = isTool ? { inputOverride: { ai_tool: [[item]] } } : { data: { main: [[item]] } };
  return workflow.expression.getParameterValue(
    expression, { resultData: { runData: { [name]: runs } } }, runIndex, 0, name,
    [item], 'manual', {}, undefined,
  );
}

function runCode(name, values, previous = {}) {
  const items = values.map(json => ({ json: copy(json) }));
  const lookup = other => {
    assert.ok(Object.hasOwn(previous, other), `Missing test upstream ${other}`);
    const supplied = previous[other].map(json => ({ json: copy(json) }));
    return { first: () => supplied[0], last: () => supplied.at(-1), all: () => supplied };
  };
  return new Function('$input', '$', nodes[name].parameters.jsCode)(
    { first: () => items[0], all: () => items }, lookup,
  ).map(item => item.json);
}

const privateReceipt = 'PRIVATE-TRANSPORT-RECEIPT';
const privateLease = 'PRIVATE-LEASE-TOKEN';
function fixture() {
  return {
    workflow_contract: 'n8n-datasheet-loop-v1',
    selected: { product_id: 'offline-test', source_hash: 'snapshot-test', revision: 'new:snapshot-test',
      product: { product_name: 'ACME M-500', brand_code: 'ACME' },
      manufacturer_hint: 'ACME', domains_hint: ['maker.example'] },
    discovery: { manufacturer: 'ACME', target_model: 'M-500', verification_reason: 'Official datasheet' },
    pdf: { document_id: 'a'.repeat(64), artifact_sha256: 'a'.repeat(64), url: 'https://maker.example/datasheet', target_model: 'M-500',
      page_count: 2, content: 'M-500 complete PDF fixture', receipt: { signature: privateReceipt, target_model: 'M-500' },
      page_texts: [
        { page: 1, plain_text: 'M-500 model header', layout_text: 'M-400  M-500', truncated: false },
        { page: 2, plain_text: 'Battery voltage 350-450 V\nFootnote: 25 C', layout_text: 'Battery voltage  350-450 V', truncated: false },
      ] },
    lease_token: privateLease, config: { vision_enabled: true, max_review_rounds: 3, save_results: true },
    page_plan: [{ page: 2, header_page: 1, applicability: 'review' }], page_cursor: 0,
    current: null, omissions: [], checkpoint_stage: 'draft', reviewed_pages: [],
  };
}

function assertNarrowPrompt(name, state, expectedKeys) {
  const prompt = resolve(name, nodes[name].parameters.text, state);
  assert.equal(typeof prompt, 'string', `${name} must produce a JSON string`);
  const parsed = JSON.parse(prompt);
  if (expectedKeys) assert.deepEqual(Object.keys(parsed).sort(), [...expectedKeys].sort(), `${name} scope`);
  assert.equal(prompt.includes(privateReceipt), false, `${name} leaked PDF receipt`);
  assert.equal(prompt.includes(privateLease), false, `${name} leaked lease`);
  assert.equal(Object.hasOwn(parsed, 'evidence_documents'), false);
  assert.equal(Object.hasOwn(parsed, 'selected'), false);
  return parsed;
}

function checkExaPayloads() {
  assert.throws(() => resolve('web_search_exa', "={{ $fromAI('arguments', 'Legacy payload', 'json') }}",
    { query: { arguments: { query: 'fixture' } } }, 0, true), /Invalid parameter key/);
  const fixtures = {
    web_search_exa: { query: 'ACME M-500 official datasheet', numResults: 1 },
    web_search_advanced_exa: { query: 'M-500 datasheet', includeDomains: ['maker.example'], numResults: 2 },
    web_fetch_exa: { urls: ['https://maker.example/model'], maxCharacters: 12345 },
  };
  for (const [name, payload] of Object.entries(fixtures)) {
    const node = nodes[name], schema = getSchema(node);
    assert.equal(node.type, 'n8n-nodes-base.httpRequestTool');
    assert.deepEqual(Object.keys(schema.shape), ['tool_input']);
    const placeholders = schema.parse({ tool_input: payload });
    for (const index of [0, 3, 25]) {
      for (const input of [placeholders, { query: placeholders }]) {
        const body = resolve(name, node.parameters.jsonBody, input, index, true);
        assert.deepEqual(body, payload, `${name} must send the JSON payload directly`);
        assert.equal(Object.hasOwn(body, 'tool_input'), false);
        assert.equal(Object.hasOwn(body, 'arguments'), false);
      }
    }
  }
  assert.equal(nodes.pv_validate_research, undefined, 'Validation must not be an AI-controlled tool');
}

function checkNarrowInputsAndTransport() {
  const state = fixture();
  const discovery = runCode('Discovery Input', [state])[0];
  const discoveryPrompt = assertNarrowPrompt('Research Agent', discovery,
    ['product', 'manufacturer_hint', 'domains_hint', 'known_datasheet_urls', 'preferred_datasheet_language', 'language_policy']);
  assert.equal(discoveryPrompt.preferred_datasheet_language, 'en');
  const initialized = runCode('Initialize Research', [{ publication_configured: false }], {
    'Claim One Item': [{ work: { product: state.selected } }],
    Configuration: [{ ...state.config, datasheet_hints: {
      [state.selected.product_id]: ['https://maker.example/verified-lead'],
      'different-product': ['https://other.example/do-not-forward'],
    } }],
  })[0];
  const hinted = runCode('Discovery Input', [initialized])[0];
  const hintedPrompt = assertNarrowPrompt('Research Agent', hinted,
    ['product', 'manufacturer_hint', 'domains_hint', 'known_datasheet_urls', 'preferred_datasheet_language', 'language_policy']);
  assert.deepEqual(hintedPrompt.known_datasheet_urls, ['https://maker.example/verified-lead']);
  assert.equal(JSON.stringify(hintedPrompt).includes('do-not-forward'), false);
  const planned = runCode('Prepare Page Plan', [state])[0];
  assertNarrowPrompt('Locate Datasheet Pages', planned, ['target_model', 'manufacturer', 'pages']);
  const chosen = runCode('Choose Page', [state])[0];
  for (const name of ['Extract Text Parameters', 'Extract Page Parameters']) {
    const payload = assertNarrowPrompt(name, chosen,
      ['page', 'header_page', 'target_model', 'manufacturer', 'plain_text', 'layout_text', 'header_text', 'header_reference', 'text_truncated']);
    assert.equal(payload.page, 2);
    assert.equal(payload.header_text, 'M-500');
    assert.equal(payload.header_reference.role, 'model_column_reference_only');
    assert.equal(payload.header_text.includes('350-450'), false);
    assert.ok(payload.plain_text.includes('Footnote: 25 C'));
  }
  chosen.current.rows = [{ row_id: 'row-1', source_label: 'Battery voltage', value: '350-450', unit: 'V',
    section: 'Battery', conditions: '25 C', footnotes: ['Footnote: 25 C'], page: 2, model: 'M-500', scope: 'model' }];
  chosen.current.render_receipts = [{ signature: privateReceipt }];
  const audit = runCode('Prepare Audit', [chosen])[0];
  for (const name of ['Audit Text Parameters', 'Audit Page Parameters']) {
    const payload = assertNarrowPrompt(name, audit,
      ['page', 'revision', 'header_page', 'target_model', 'plain_text', 'layout_text', 'header_text',
        'header_reference', 'text_truncated', 'parameters', 'previous_feedback', 'previous_omissions']);
    assert.equal(payload.parameters[0].row_id, 'row-1');
  }
  const snapshot = runCode('Progress Snapshot', [audit])[0];
  for (const name of ['Render Extraction Pages', 'Render Audit Pages']) {
    const body = resolve(name, nodes[name].parameters.jsonBody, audit);
    assert.deepEqual(body.pages, [2], 'Only the current page can be rendered as model image input');
  }
  for (const index of [0, 3, 25]) {
    const body = resolve('Checkpoint Progress', nodes['Checkpoint Progress'].parameters.jsonBody, snapshot, index);
    assert.deepEqual(body, { product_id: state.selected.product_id, source_hash: state.selected.source_hash,
      stage: snapshot.checkpoint_stage, context: snapshot });
    assert.ok(JSON.stringify(body).includes(privateReceipt), 'Checkpoint must preserve provenance internally');
  }
  chosen.reviewed_pages = [{ page: 2, rows: chosen.current.rows, receipts: [{ signature: privateReceipt }] }];
  chosen.decision = { expected_revision: 'untrusted-model-revision' };
  const built = runCode('Build Decision', [chosen])[0];
  assert.equal(built.decision.expected_revision, state.selected.revision);
  const expected = { product_id: state.selected.product_id, source_hash: state.selected.source_hash,
    decision: built.decision, evidence_documents: built.evidence_documents };
  for (const name of ['Validate Candidate', 'Validate Parameters Only', 'Save Research']) {
    assert.equal(nodes[name].type, 'n8n-nodes-base.httpRequest');
    for (const index of [0, 3, 25]) {
      assert.deepEqual(resolve(name, nodes[name].parameters.jsonBody, built, index), expected,
        `${name} must use the current workflow item without an AI placeholder or stale run reference`);
    }
  }
  return built;
}

function checkTranslations(built) {
  const state = copy(built);
  state.decision.parameters = Array.from({ length: 63 }, (_, i) => ({
    ...built.decision.parameters[0], parameter_id: `p${i + 1}`, source_label: `Fixture row ${i + 1}`,
  }));
  const original = copy(state.decision.parameters);
  const batches = runCode('Prepare Translation Batches', [state]);
  assert.deepEqual(batches.flatMap(batch => batch.parameters), original, 'Translation batching must not lose rows');
  for (const batch of batches) {
    assertNarrowPrompt('Translate Parameters', batch,
      ['batch', 'model', 'manufacturer', 'datasheet_language', 'parameters']);
  }
  const merged = runCode('Merge Translations', [{ error: { message: 'Offline simulated model failure' } }],
    { 'Build Decision': [state] })[0];
  assert.deepEqual(merged.decision.parameters, original);
  assertNarrowPrompt('Audit Translations', merged, ['model', 'datasheet_language', 'parameters', 'translations']);
  const translated = { parameter_id: original[0].parameter_id, name_zh: '电池电压', section_zh: '电池',
    value_zh: '350-450', conditions_zh: '25 C 时', footnotes_zh: ['脚注：25 C'] };
  const translatedMerge = runCode('Merge Translations', [{ text: JSON.stringify({ analysis: {
    translations: [translated], sections: [], overall_limitations_zh: [],
  } }) }], { 'Build Decision': [state] })[0];
  const translatedPrompt = assertNarrowPrompt('Audit Translations', translatedMerge,
    ['model', 'datasheet_language', 'parameters', 'translations']);
  assert.deepEqual(translatedPrompt.translations[0], translated);
  const translatedAudit = runCode('Read Translation Audit', [{ text: JSON.stringify({
    approved: true, translations: translatedPrompt.translations,
  }) }], { 'Merge Translations': [translatedMerge] })[0];
  assert.deepEqual(translatedAudit.decision.analysis.translations[0], translated);
  assert.deepEqual(translatedAudit.decision.parameters, original);
  const partialState = copy(state);
  partialState.decision.parameters[0].footnotes = ['First source note', 'Second source note', 'Third source note'];
  const partialTranslation = { ...translated, footnotes_zh: ['第一条译文', null, '第三条译文'] };
  const partialMerge = runCode('Merge Translations', [{ text: JSON.stringify({ analysis: {
    translations: [partialTranslation], sections: [], overall_limitations_zh: [],
  } }) }], { 'Build Decision': [partialState] })[0];
  const partialPrompt = assertNarrowPrompt('Audit Translations', partialMerge,
    ['model', 'datasheet_language', 'parameters', 'translations']);
  assert.deepEqual(partialPrompt.translations[0].footnotes_zh, ['第一条译文', '', '第三条译文']);
  const partialAudit = runCode('Read Translation Audit', [{ text: JSON.stringify({
    approved: true, translations: partialPrompt.translations,
  }) }], { 'Merge Translations': [partialMerge] })[0];
  assert.deepEqual(partialAudit.decision.analysis.translations[0].footnotes_zh, ['第一条译文', '', '第三条译文']);
  assert.deepEqual(partialAudit.decision.parameters, partialState.decision.parameters);
  for (const response of [
    { error: { message: 'Model request failed' }, text: JSON.stringify({ approved: true, translations: partialPrompt.translations }) },
    { text: JSON.stringify({ error: 'Model request failed', approved: true, translations: partialPrompt.translations }) },
  ]) {
    const failed = runCode('Read Translation Audit', [response], { 'Merge Translations': [partialMerge] })[0];
    assert.equal(failed.decision.analysis, undefined);
    assert.deepEqual(failed.decision.parameters, partialState.decision.parameters);
  }
  const audited = runCode('Read Translation Audit', [{ text: '{invalid JSON' }],
    { 'Merge Translations': [merged] })[0];
  assert.deepEqual(audited.decision.parameters, original);
  audited.decision.analysis = { unsupported: true };
  const retried = runCode('Read Validation', [{ ok: false, valid: false, errors: ['analysis format'] }],
    { 'Read Translation Audit': [audited] })[0];
  assert.equal(retried.retry_without_translations, true);
  assert.equal(Object.hasOwn(retried.decision, 'analysis'), false);
  assert.deepEqual(retried.decision.parameters, original, 'Translation rejection must retain every confirmed parameter');
}

function checkDiscoveryModelNormalization() {
  const input = fixture(), originalName = 'HUAWEI SUN2000L - 3.68KTL';
  input.selected.manufacturer_hint = 'Huawei';
  input.selected.product = { brand_code: 'HUAWEI', product_name: originalName };
  const normalized = runCode('Read Discovery', [{ output: JSON.stringify({
    found: true, manufacturer: 'Huawei', target_model: originalName, url: 'https://maker.example/pdf',
  }) }], { 'Discovery Input': [input] })[0];
  assert.equal(normalized.discovery.target_model, 'SUN2000L-3.68KTL');
  assert.equal(normalized.selected.product.product_name, originalName);
  const download = resolve('Read Original PDF', nodes['Read Original PDF'].parameters.jsonBody, normalized);
  assert.equal(download.target_model, 'SUN2000L-3.68KTL', 'A fresh PDF receipt is requested for the manufacturer model');
  const good = copy(normalized);
  good.pdf.target_model = 'SUN2000L-3.68KTL';
  good.pdf.receipt.target_model = 'SUN2000L-3.68KTL';
  const resume = checkpoint => runCode('Initialize Research', [{ publication_configured: false }], {
    'Claim One Item': [{ work: { product: input.selected, checkpoint: { context: checkpoint } } }],
    Configuration: [input.config],
  })[0];
  assert.equal(resume(good).resumed, true);
  for (const mutate of [
    c => { c.discovery.target_model = originalName; c.pdf.target_model = originalName; c.pdf.receipt.target_model = originalName; },
    c => { c.pdf.target_model = 'SUN2000L-5KTL'; },
    c => { c.pdf.receipt.target_model = 'SUN2000L-5KTL'; },
  ]) {
    const mismatched = copy(good); mutate(mismatched);
    const restarted = resume(mismatched);
    assert.equal(restarted.resumed, false);
    assert.equal(restarted.pdf, null);
    assert.equal(restarted.current, null);
    assert.equal(restarted.next_action, 'discover');
    assert.deepEqual(restarted.selected, input.selected);
  }
}

function checkAll() {
  checkExaPayloads();
  checkDiscoveryModelNormalization();
  checkTranslations(checkNarrowInputsAndTransport());
  for (const [name, field] of [['Validation Passed', 'validated'], ['Save Enabled', 'save_enabled']]) {
    const expression = nodes[name].parameters.conditions.conditions[0].leftValue;
    for (const value of [true, false, 'true', null, undefined]) {
      assert.equal(resolve(name, expression, { [field]: value }), value === true,
        `${name} must permit only an explicit successful state`);
    }
  }
  const state = fixture();
  state.error = { stage: 'audit', message: 'Offline incomplete review' };
  const incomplete = runCode('Format Result', [{ completed: false, published: false }],
    { 'Prepare Result': [state] })[0];
  const output = runCode('Research Result', [{ released: true }], { 'Format Result': [incomplete] })[0];
  assert.equal(output.outcome, 'incomplete');
  assert.equal(output.saved, false);
  assert.equal(output.session_released, true);
}

(async () => {
  checkAll();
  try {
    await Expression.initExpressionEngine({ engine: 'vm', bridgeTimeout: 5000, bridgeMemoryLimit: 64, poolSize: 1 });
    await workflow.expression.withIsolate(async () => checkAll());
  } finally {
    await Expression.disposeExpressionEngine();
  }
  console.log('Native n8n legacy + VM passed: 3 Exa tool payloads; current-item checkpoint, validation and save; narrow translation-only prompts; translation failure preserves all 63 rows. No network calls.');
})().catch(error => { console.error(error); process.exitCode = 1; });
