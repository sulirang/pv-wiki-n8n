/* Embedded in an n8n Code node. No imports, runtime APIs, or model calls.
 * current is workflow-owned state; only review comes from the auditor model.
 * Supply actual image-delivery state in has_images, not a model assertion.
 * On done=false, feed the returned current (including revision) into a new audit.
 * On done=true, rows contains independently confirmed rows. If the final audit
 * envelope is malformed, unchanged rows confirmed in prior history may survive.
 * row_states is the audit ledger; a candidate's status:'confirmed' is not proof.
 * Invalid review envelopes set failed=true and consume one bounded review round.
 */
function applyPageAudit(current, review, opts) {
  'use strict';
  const clone = value => JSON.parse(JSON.stringify(value));
  const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  const text = value => typeof value === 'string' ? value.trim() : (typeof value === 'number' && Number.isFinite(value) ? String(value) : '');
  const fold = value => text(value).normalize('NFKC').toLocaleLowerCase('en').replace(/[\u2010-\u2015\u2212]/g, '-');
  const compact = value => fold(value).replace(/\s+/g, '');
  const modelKey = value => compact(value);
  const canonical = value => {
    if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
    if (object(value)) return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
    return JSON.stringify(value);
  };
  const messages = value => Array.isArray(value) ? value.filter(item => typeof item === 'string' && item.trim()).map(item => item.trim()) : [];
  const start = object(current) ? clone(current) : {};
  const maxRounds = Number.isInteger(opts?.max_rounds) && opts.max_rounds > 0 ? opts.max_rounds : 3;
  const previousRound = Number.isInteger(start.round) && start.round >= 0 ? start.round : 0;
  const revision = Number.isInteger(start.revision) && start.revision >= 0 ? start.revision : 0;
  const result = {...start, round: previousRound + 1, revision, done: false, failed: false,
    rows: Array.isArray(start.rows) ? start.rows : [], omissions: messages(start.omissions),
    history: Array.isArray(start.history) ? start.history : [], confirmed_count: 0,
    row_states: Object.create(null), review_issues: [], region: null, feedback: ''};
  const omit = reason => { if (!result.omissions.includes(reason)) result.omissions.push(reason); };
  const exhausted = () => result.round >= maxRounds;
  const resolveHistoricalTechnicalOmissions = () => {
    const technicalReasons = new Set([
      'Evidence must locate this exact PDF page',
      'Parameter page or model does not match the task',
      'Parameter scope must be model or shared',
      'Shared parameter does not explicitly apply to the target model',
      'Parameter applicability excludes the target model',
      'Parameter label and value are required',
      'Evidence needs a nonempty raw label, value, and model header locator',
      'Evidence header does not locate the target model or its explicitly shared series',
      'The evidence value locator does not contain the final value',
      'Locators are not supported by this page text and no page images were supplied',
    ]);
    for (const entry of [...result.history]) {
      if (entry.action !== 'omitted' || entry.review_outcome || !technicalReasons.has(entry.reason)
          || !object(entry.before) || !text(entry.before.source_label)) continue;
      const old = entry.before, resolved = result.rows.find(row => result.row_states[row.row_id]?.status === 'confirmed'
        && row.page === old.page && modelKey(row.model) === modelKey(old.model)
        && compact(row.source_label) === compact(old.source_label));
      const message = `${entry.row_id}: ${entry.reason}`;
      if (!resolved || !result.omissions.includes(message)) continue;
      result.omissions = result.omissions.filter(value => value !== message);
      result.history.push({round: result.round, revision: result.revision, row_id: entry.row_id,
        action: 'evidence_issue_resolved', resolved_by_row_id: resolved.row_id,
        before: clone(old), after: clone(resolved), reason: entry.reason});
    }
  };
  const badReview = reason => {
    result.failed = true;
    result.feedback = reason;
    result.done = exhausted();
    result.history.push({round: result.round, revision, action: 'invalid_review', reason});
    if (result.done) {
      const previousStates = object(start.row_states) ? start.row_states : {};
      const previousHistory = Array.isArray(start.history) ? start.history : [];
      const retained = [];
      for (const row of result.rows) {
        const state = Object.hasOwn(previousStates, row.row_id) ? previousStates[row.row_id] : null;
        const earlierConfirmation = previousRound > 0 && object(state) && state.status === 'confirmed'
          && previousHistory.some(entry => object(entry) && entry.action === 'confirmed'
            && entry.row_id === row.row_id && Number.isInteger(entry.round) && entry.round <= previousRound
            && entry.revision === state.reviewed_revision && object(entry.before) && object(entry.evidence)
            && canonical(entry.before) === canonical(row) && canonical(entry.evidence) === canonical(state.evidence));
        if (earlierConfirmation) {
          retained.push(row); result.row_states[row.row_id] = clone(state); result.confirmed_count++;
        } else omit(`${text(row.row_id) || 'Unknown row'}: not confirmed by the final audit (${reason})`);
      }
      result.rows = retained;
      resolveHistoricalTechnicalOmissions();
    }
    return result;
  };
  if (!Number.isInteger(start.page) || start.page < 1 || !text(start.model)) return badReview('Invalid page task or target model');
  if (!object(review) || review.page !== start.page || review.revision !== revision) return badReview('Audit page or revision does not match the current candidate');
  if (!['target', 'shared', 'unrelated', 'uncertain'].includes(review.applicability)) return badReview('Audit applicability is invalid');
  if (!Array.isArray(review.checks) || !Array.isArray(review.additions) || !object(review.coverage)
      || typeof review.coverage.complete !== 'boolean' || !Array.isArray(review.coverage.missing)) return badReview('Audit must contain checks, additions, and explicit coverage');
  const ids = new Set();
  for (const row of result.rows) {
    if (!object(row) || !text(row.row_id) || ids.has(row.row_id)) return badReview('Candidate row IDs must be nonempty and unique');
    ids.add(row.row_id);
  }
  const checks = new Map();
  for (const check of review.checks) {
    if (!object(check) || !ids.has(check.row_id) || checks.has(check.row_id)) return badReview('Audit must check every candidate row ID exactly once');
    if (!['confirmed', 'corrected', 'unsupported', 'uncertain'].includes(check.outcome)) return badReview('Audit row outcome is invalid');
    if (check.outcome === 'corrected' && (!object(check.corrected_row) || !text(check.reason))) return badReview('Corrections need corrected_row and a reason');
    checks.set(check.row_id, check);
  }
  if (checks.size !== ids.size) return badReview('Audit must check every candidate row ID exactly once');
  if (review.additions.some(addition => !object(addition))) return badReview('Added parameters must be objects');
  result.applicability = review.applicability;
  result.coverage = {complete: review.coverage.complete, missing: messages(review.coverage.missing)};
  result.omissions.push(...messages(review.omissions).filter(value => !result.omissions.includes(value)));
  const region = review.region;
  if (review.needs_zoom === true && Array.isArray(region) && region.length === 4
      && region.every(value => Number.isFinite(value) && value >= 0 && value <= 1)
      && region[0] < region[2] && region[1] < region[3]) result.region = [...region];
  const plain = fold(start.plain_text || '');
  const located = (source, quote, modelLocator = false) => {
    const needle = compact(quote);
    if (!needle) return false;
    const original = fold(source), positions = [];
    let haystack = '';
    for (let index = 0; index < original.length; index++) {
      if (!/\s/.test(original[index])) { haystack += original[index]; positions.push(index); }
    }
    let at = haystack.indexOf(needle);
    while (at !== -1) {
      const before = original[positions[at] - 1] || '', after = original[positions[at + needle.length - 1] + 1] || '';
      const numberBoundary = (/^[0-9]/.test(needle) && /[0-9.,+-]/.test(before))
        || (/[0-9]$/.test(needle) && /[0-9.,]/.test(after));
      const modelBoundary = modelLocator && (/[a-z0-9._-]/.test(before) || /[a-z0-9._-]/.test(after));
      if (!numberBoundary && !modelBoundary) return true;
      at = haystack.indexOf(needle, at + 1);
    }
    return false;
  };
  const contains = quote => located(plain, quote);
  const compressedModels = header => {
    // A complete prefix, an explicit numeric variant list, and one common
    // suffix can identify several full models without spelling each one out.
    // Expand first, then compare full models; a near-prefix is never equivalent.
    const match = compact(header).match(/^(.*?[^0-9.])([0-9]+(?:\.[0-9]+)?(?:\/[0-9]+(?:\.[0-9]+)?)+)([-_]?[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*)$/);
    if (!match || !/[a-z]/.test(match[1])) return null;
    return match[2].split('/').map(variant => match[1] + variant + match[3]);
  };
  const includesTarget = models => Array.isArray(models) && models.includes(modelKey(start.model));
  const groupedNumber = value => fold(value).replace(
    /(^|[^0-9.,])([1-9][0-9]{0,2}(?:,[0-9]{3})+(?:\.[0-9]+)?)(?=$|[^0-9.,])/g,
    (_, before, number) => before + number.replace(/,/g, ''));
  const modelParts = text(start.model).split(/[\s\u2010-\u2015-]+/).filter(Boolean);
  // A PDF often separates a series prefix and the exact variant in its header.
  const reference = fold(start.header_text || '');
  const identifiesTarget = source => located(source, start.model, true)
    || (modelParts.length > 1 && modelParts.every(part => located(source, part, true)))
    || source.replace(/\s*\/\s*/g, '/').split(/\s+/).some(token => includesTarget(compressedModels(token.replace(/^[\[(]+|[\]),;:]+$/g, ''))));
  // Cross-page context contains only model labels. It can identify a continued
  // column, never prove that a parameter label/value occurs on this page.
  const targetInText = identifiesTarget(plain) || identifiesTarget(reference);
  const cleanRow = raw => {
    const row = clone(raw);
    for (const field of ['evidence', 'review_status', 'audit_status', 'confirmed_revision']) delete row[field];
    row.status = 'confirmed';
    return row;
  };
  const evidenceFor = (row, evidence) => {
    if (!object(evidence) || evidence.page !== start.page) return {error: 'Evidence must locate this exact PDF page'};
    if (row.page !== start.page || modelKey(row.model) !== modelKey(start.model)) return {error: 'Parameter page or model does not match the task'};
    if (!['model', 'shared'].includes(row.scope)) return {error: 'Parameter scope must be model or shared'};
    if (row.scope === 'shared' && (!Array.isArray(row.applies_to_models)
        || !row.applies_to_models.some(model => modelKey(model) === modelKey(start.model)))) return {error: 'Shared parameter does not explicitly apply to the target model'};
    if (row.scope === 'model' && Array.isArray(row.applies_to_models)
        && !row.applies_to_models.some(model => modelKey(model) === modelKey(start.model))) return {error: 'Parameter applicability excludes the target model'};
    if (!text(row.source_label) || !text(row.value)) return {error: 'Parameter label and value are required'};
    if (!text(evidence.raw_label) || !text(evidence.raw_value) || !text(evidence.column_header)) return {error: 'Evidence needs a nonempty raw label, value, and model header locator'};
    const header = fold(evidence.column_header), expanded = compressedModels(header);
    const targetHeader = expanded ? includesTarget(expanded) : located(header, start.model, true)
      || (modelParts.length > 1 && modelKey(header) === modelKey(modelParts[modelParts.length - 1]));
    const sharedHeader = !expanded && row.scope === 'shared' && modelParts.length > 1 && located(header, modelParts.slice(0, -1).join('-'), true);
    if (!targetHeader && !sharedHeader) return {error: 'Evidence header does not locate the target model or its explicitly shared series'};
    // These are short locators, not an exact-quote requirement for a whole table row.
    // Comparison tolerates PDF whitespace and complete thousands groups only.
    // Original values are retained; decimal commas, signs and ranges stay intact.
    const valueLocated = located(groupedNumber(evidence.raw_value), groupedNumber(row.value));
    if (!valueLocated) return {error: 'The evidence value locator does not contain the final value'};
    const textBacked = contains(row.source_label) && contains(evidence.raw_label)
      && located(groupedNumber(plain), groupedNumber(evidence.raw_value))
      && (located(plain, evidence.column_header, true) || located(reference, evidence.column_header, true) || ((modelKey(evidence.column_header) === modelKey(start.model) || includesTarget(expanded)) && targetInText))
      && targetInText;
    if (!textBacked && start.has_images !== true) return {error: 'Locators are not supported by this page text and no page images were supplied'};
    return {basis: textBacked ? 'text' : 'visual', evidence: clone(evidence)};
  };
  const history = (row, action, evidence, reason, after) => result.history.push({round: result.round,
    revision, row_id: row.row_id, action, evidence: object(evidence) ? clone(evidence) : null,
    before: clone(row), ...(after ? {after: clone(after)} : {}), reason: text(reason)});
  const next = [];
  let changed = false;
  let additionsCounter = Number.isInteger(start.next_row_number) ? start.next_row_number : 1;
  const usedIds = new Set([...ids, ...result.history.map(item => item.row_id).filter(Boolean)]);
  const newId = () => {
    let id;
    do { id = `page-${start.page}-added-${additionsCounter++}`; } while (usedIds.has(id));
    usedIds.add(id); return id;
  };
  const accept = (row, check, addition) => {
    const candidate = cleanRow(row);
    const proof = evidenceFor(candidate, check.evidence);
    if (proof.error) {
      // A malformed locator is repairable metadata, not a finding that the
      // underlying parameter is unsupported. Keep the exact ID and candidate.
      const original = addition ? null : result.rows.find(item => item.row_id === candidate.row_id);
      const priorState = object(start.row_states) && Object.hasOwn(start.row_states, candidate.row_id) ? start.row_states[candidate.row_id] : null;
      const pendingCorrection = !addition && check.outcome === 'corrected' && original
        ? {before: {value: original.value, unit: original.unit || ''}, after: {value: candidate.value, unit: candidate.unit || ''}, reason: text(check.reason)}
        : priorState?.pending_correction;
      history(original || candidate, 'evidence_pending', check.evidence, proof.error, candidate);
      result.review_issues.push({row_id: candidate.row_id, reason: proof.error});
      next.push(candidate);
      result.row_states[candidate.row_id] = {status: 'pending', issue: proof.error,
        evidence: object(check.evidence) ? clone(check.evidence) : null, reviewed_revision: revision,
        ...(object(pendingCorrection) ? {pending_correction: clone(pendingCorrection)} : {})};
      changed = true;
      return;
    }
    const pending = addition || check.outcome === 'corrected';
    if (pending) {
      changed = true;
      candidate.source_kind = proof.basis === 'visual' ? 'visual' : 'parser';
      const before = addition ? null : result.rows.find(item => item.row_id === candidate.row_id);
      if (!addition && proof.basis === 'visual') {
        candidate.correction = {before: {value: before.value, unit: before.unit || ''},
          after: {value: candidate.value, unit: candidate.unit || ''}, reason: text(check.reason)};
      } else delete candidate.correction;
      history(before || candidate, addition ? 'added_pending' : 'corrected_pending', proof.evidence, check.reason, candidate);
    } else {
      if (proof.basis === 'visual' || !['parser', 'visual'].includes(candidate.source_kind)) candidate.source_kind = proof.basis === 'visual' ? 'visual' : 'parser';
      const priorState = object(start.row_states) && Object.hasOwn(start.row_states, candidate.row_id) ? start.row_states[candidate.row_id] : null;
      if (proof.basis === 'visual' && priorState?.status === 'pending' && object(priorState.pending_correction)
          && canonical(priorState.pending_correction.after) === canonical({value: candidate.value, unit: candidate.unit || ''})) {
        candidate.correction = clone(priorState.pending_correction);
      }
      if (start.has_images !== true) {
        // A text check can establish a row anew without falsely claiming a visual correction.
        candidate.source_kind = 'parser';
        delete candidate.correction;
      }
      history(candidate, 'confirmed', proof.evidence, check.reason);
      result.confirmed_count++;
    }
    next.push(candidate);
    result.row_states[candidate.row_id] = {status: pending ? 'pending' : 'confirmed', evidence: proof.evidence,
      basis: proof.basis, reviewed_revision: revision};
  };
  for (const original of result.rows) {
    const check = checks.get(original.row_id);
    if (review.applicability === 'unrelated' || review.applicability === 'uncertain'
        || check.outcome === 'unsupported' || check.outcome === 'uncertain') {
      const reason = text(check.reason) || `Page applicability is ${review.applicability}; parameter is ${check.outcome}`;
      omit(`${original.row_id}: ${reason}`); history(original, 'omitted', check.evidence, reason);
      result.history[result.history.length - 1].review_outcome = ['unrelated', 'uncertain'].includes(review.applicability) ? review.applicability : check.outcome;
      changed = true;
      continue;
    }
    if (check.outcome === 'corrected') {
      // Preserve untouched conditions, footnotes and grouping when the reviewer supplies a patch.
      const corrected = {...original, ...check.corrected_row, row_id: original.row_id};
      accept(corrected, check, false);
    } else accept(original, check, false);
  }
  if (review.applicability === 'target' || review.applicability === 'shared') {
    for (const addition of review.additions) {
      const row = {...addition, row_id: newId()};
      // Identity metadata is normally injected by Code, not authored by the model.
      // Only absence receives a default: an explicit wrong page/model still fails.
      for (const [field, fallback] of Object.entries({page: start.page, model: start.model,
        conditions: '', footnotes: [], unit: ''})) {
        if (row[field] === undefined) row[field] = clone(fallback);
      }
      delete row.evidence;
      accept(row, {outcome: 'corrected', evidence: addition.evidence, reason: text(addition.reason) || 'Missing parameter identified during page audit'}, true);
    }
  } else if (review.additions.length) omit('Additions were ignored because the page does not clearly apply to the target model');
  result.rows = next;
  result.next_row_number = additionsCounter;
  if (changed) result.revision = revision + 1;
  const hasPending = Object.values(result.row_states).some(state => state.status === 'pending');
  const hasGap = review.applicability === 'target' || review.applicability === 'shared'
    ? !result.coverage.complete || result.coverage.missing.length > 0 || review.needs_zoom === true : false;
  result.done = exhausted() || (!hasPending && !hasGap);
  if (result.done) {
    // One terminal gate owns publication eligibility. Never return pending rows
    // merely because the retry budget ended after a successful review response.
    const confirmations = new Map(result.history.filter(entry => entry.action === 'confirmed'
      && entry.round === result.round && entry.revision === revision).map(entry => [entry.row_id, entry]));
    const confirmed = [];
    for (const row of result.rows) {
      const state = result.row_states[row.row_id], record = confirmations.get(row.row_id);
      if (state?.status === 'confirmed' && object(record) && object(record.evidence)
          && canonical(record.before) === canonical(row) && canonical(record.evidence) === canonical(state.evidence)) {
        confirmed.push(row);
      } else {
        const reason = state?.issue ? `Unresolved evidence metadata: ${state.issue}`
          : `Parameter could not receive an independent recheck within ${maxRounds} rounds`;
        omit(`${row.row_id}: ${reason}`);
        history(row, 'unreviewed_change_omitted', state?.evidence, reason);
        delete result.row_states[row.row_id];
      }
    }
    result.rows = confirmed;
    result.confirmed_count = confirmed.length;
    resolveHistoricalTechnicalOmissions();
  }
  if (result.done && hasGap) {
    for (const gap of result.coverage.missing) omit(`Unresolved page coverage: ${gap}`);
    if (!result.coverage.complete && !result.coverage.missing.length) omit(`Page ${start.page}: coverage is incomplete`);
    if (review.needs_zoom === true) omit(`Page ${start.page}: requested visual enlargement was not completed`);
  }
  const issueGroups = new Map();
  for (const issue of result.review_issues) {
    if (!issueGroups.has(issue.reason)) issueGroups.set(issue.reason, []);
    issueGroups.get(issue.reason).push(issue.row_id);
  }
  result.feedback = result.done ? '' : 'Recheck every retained row and all changed or added rows against the original page; resolve the listed coverage gaps'
    + (issueGroups.size ? '\nRepair evidence metadata without dropping supported parameters:\n'
      + [...issueGroups].map(([reason, rowIds]) => rowIds.join(', ') + ': ' + reason).join('\n') : '');
  return result;
}
