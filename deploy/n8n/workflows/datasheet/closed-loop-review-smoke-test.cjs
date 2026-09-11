'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, 'closed_loop_review.js'), 'utf8');
const apply = new Function(source + '\nreturn applyPageAudit;')();
const copy = value => JSON.parse(JSON.stringify(value));
const MODEL = 'SUN2000L-3.68KTL';
const text = `SUN2000L
2KTL 3KTL 3.68KTL 4.6KTL 5KTL
Maximum efficiency 98.5 %
Battery voltage 350~450 Vdc
Input voltage 600 / 495 V
Maximum MPPT current 11 A 11 A 11 A
Power output 3680 W
Power output 3400 W
AC short-circuit protection Yes
Maximum total harmonic distortion ≤3 %
Only for PV strings. When using an LG battery, the maximum voltage is 495 V.
`;
const row = (id = 'battery', extra = {}) => ({row_id: id, parameter_id: id,
  source_label: 'Battery voltage', value: '350~450', unit: 'Vdc', section: 'Battery compatibility',
  model: MODEL, scope: 'shared', applies_to_models: [MODEL, 'SUN2000L-5KTL'], page: 4,
  status: 'confirmed', source_kind: 'parser', conditions: '', footnotes: [], ...extra});
const task = rows => ({page: 4, header_page: 4, model: MODEL, rows: rows || [row()], round: 0,
  omissions: [], history: [], has_images: false, render_receipts: [], plain_text: text, revision: 0});
const proof = (item, extra = {}) => ({page: 4, raw_label: item.source_label,
  raw_value: String(item.value) + (item.unit ? ' ' + item.unit : ''), column_header: MODEL,
  footnotes: item.footnotes || [], ...extra});
const check = (item, extra = {}) => ({row_id: item.row_id, outcome: 'confirmed', evidence: proof(item), reason: 'Matches the original page', ...extra});
const review = (current, extra = {}) => ({page: current.page, revision: current.revision,
  applicability: 'target', checks: current.rows.map(item => check(item)), additions: [], omissions: [],
  coverage: {complete: true, missing: []}, needs_zoom: false, region: null, ...extra});
let tests = 0;
function test(name, run) { run(); tests++; }

test('one official page and one row can finish; inputs are not mutated', () => {
  const input = task(), audit = review(input), before = JSON.stringify([input, audit]);
  const output = apply(input, audit);
  assert.equal(output.done, true); assert.equal(output.confirmed_count, 1);
  assert.deepEqual(output.rows, input.rows); assert.equal(output.rows[0].source_kind, 'parser');
  assert.equal(JSON.stringify([input, audit]), before);
});
test('page and revision bind the audit to its candidate', () => {
  for (const patch of [{page: 5}, {revision: 1}, {revision: '0'}]) {
    const input = task(), output = apply(input, review(input, patch));
    assert.equal(output.failed, true); assert.equal(output.done, false); assert.equal(output.confirmed_count, 0);
  }
});
test('every ID must be checked exactly once', () => {
  const input = task();
  for (const checks of [[], [check(input.rows[0]), check(input.rows[0])], [{...check(input.rows[0]), row_id: 'other'}]]) {
    assert.equal(apply(input, review(input, {checks})).failed, true);
  }
});
test('candidate IDs are unique, and duplicate audits cannot approve another row', () => {
  const input = task([row(), row()]);
  assert.equal(apply(input, review(input)).failed, true);
});
test('runtime candidate status is not an independent confirmation', () => {
  const input = task(), output = apply(input, review(input, {checks: []}), {max_rounds: 1});
  assert.equal(output.done, true); assert.equal(output.failed, true); assert.deepEqual(output.rows, []);
});
test('text correction must be rechecked and preserves unrelated values and all context', () => {
  const original = row('battery', {value: '300~450', conditions: 'Connected battery', footnotes: ['Battery interface'], subsection: 'Voltage'});
  const unchanged = row('efficiency', {source_label: 'Maximum efficiency', value: '98.5', unit: '%'});
  const input = task([original, unchanged]);
  const corrected = {...original, value: '350~450'};
  const output = apply(input, review(input, {checks: [check(original, {outcome: 'corrected', corrected_row: {value: '350~450'}, evidence: proof(corrected), reason: 'The first digit is 5, not 0'}), check(unchanged)]}));
  assert.equal(output.done, false); assert.equal(output.revision, 1); assert.equal(output.confirmed_count, 1);
  assert.equal(output.rows[0].value, '350~450'); assert.equal(output.rows[0].source_kind, 'parser');
  assert.equal(output.rows[0].correction, undefined); assert.deepEqual(output.rows[0].footnotes, original.footnotes);
  assert.equal(output.rows[0].conditions, original.conditions); assert.equal(output.rows[0].subsection, 'Voltage');
  assert.deepEqual(output.rows[1], unchanged); assert.equal(output.row_states.battery.status, 'pending');
  assert.equal(output.history[0].before.value, '300~450'); assert.equal(output.history[0].after.value, '350~450');
  const verified = apply(output, review(output));
  assert.equal(verified.done, true); assert.equal(verified.confirmed_count, 2); assert.equal(verified.revision, 1);
});
test('stale pre-correction review cannot confirm corrected values', () => {
  const input = task([row('battery', {value: '300~450'})]);
  const fixed = apply(input, review(input, {checks: [check(input.rows[0], {outcome: 'corrected', corrected_row: {value: '350~450'}, evidence: proof(row()), reason: 'Correct lower bound'})]}));
  assert.equal(apply(fixed, review(input)).failed, true);
});
test('new rows receive fresh IDs, no same-name deduplication, and require recheck', () => {
  const input = task([row('power-stc', {source_label: 'Power output', value: '3680', unit: 'W', conditions: 'STC'})]);
  const addition = row('power-stc', {source_label: 'Power output', value: '3400', unit: 'W', conditions: 'NMOT'});
  const output = apply(input, review(input, {additions: [{...addition, evidence: proof(addition)}]}));
  assert.equal(output.rows.length, 2); assert.notEqual(output.rows[1].row_id, input.rows[0].row_id);
  assert.equal(output.rows[0].conditions, 'STC'); assert.equal(output.rows[1].conditions, 'NMOT');
  assert.equal(output.row_states[output.rows[1].row_id].status, 'pending'); assert.equal(output.done, false);
  assert.equal(apply(output, review(output)).confirmed_count, 2);
});
test('additions receive absent identity and optional defaults, not invented model metadata', () => {
  const input = task(), addition = {source_label: 'Maximum total harmonic distortion', value: '≤3',
    section: 'AC output', scope: 'shared', applies_to_models: [MODEL],
    evidence: {page: 4, raw_label: 'Maximum total harmonic distortion', raw_value: '≤3 %', column_header: MODEL, footnotes: []}};
  const output = apply(input, review(input, {additions: [addition]}));
  assert.equal(output.rows.length, 2); const added = output.rows[1];
  assert.equal(added.page, 4); assert.equal(added.model, MODEL); assert.equal(added.unit, '');
  assert.equal(added.conditions, ''); assert.deepEqual(added.footnotes, []);
  assert.equal(output.row_states[added.row_id].status, 'pending');
});
test('addition defaults never replace explicit invalid page or model values', () => {
  for (const patch of [{page: 5}, {page: null}, {model: 'SUN2000L-5KTL'}, {model: null}]) {
    const input = task(), addition = {...row('bad'), ...patch, evidence: proof(row())};
    const output = apply(input, review(input, {additions: [addition]}), {max_rounds: 1});
    assert.equal(output.rows.length, 1); assert.equal(output.confirmed_count, 1);
    assert.ok(output.omissions.some(value => value.includes('page or model')));
  }
});
test('unsupported and uncertain rows are removed with reasons, leaving confirmed rows', () => {
  const input = task([row(), row('emc', {source_label: 'EMC', value: 'EN 61000', unit: ''}), row('unknown')]);
  const output = apply(input, review(input, {checks: [check(input.rows[0]), check(input.rows[1], {outcome: 'unsupported', reason: 'No EMC row exists'}), check(input.rows[2], {outcome: 'uncertain', reason: 'Column unclear'})]}));
  assert.equal(output.done, true); assert.deepEqual(output.rows.map(item => item.row_id), ['battery']);
  assert.ok(output.omissions.some(item => item.includes('No EMC row exists')));
  assert.ok(output.omissions.some(item => item.includes('Column unclear')));
});
test('unrelated optimizer page cannot contribute inverter rows', () => {
  const input = task(), output = apply(input, review(input, {applicability: 'unrelated', additions: [{...row('injected'), evidence: proof(row())}]}));
  assert.equal(output.done, true); assert.deepEqual(output.rows, []); assert.equal(output.confirmed_count, 0);
  assert.equal(output.applicability, 'unrelated');
});
test('review cannot move evidence to a different page', () => {
  const input = task(), output = apply(input, review(input, {checks: [check(input.rows[0], {evidence: proof(input.rows[0], {page: 5})})]}), {max_rounds: 1});
  assert.equal(output.rows.length, 0); assert.match(output.omissions.join(' '), /exact PDF page/);
});
test('sibling model and missing shared applicability are rejected', () => {
  for (const patch of [{model: 'SUN2000L-5KTL'}, {model: 'SUN2000L1-3.68KTL'}, {model: 'SUN2000L-3.6KTL'}, {applies_to_models: []}, {applies_to_models: undefined}]) {
    const input = task([row('test', patch)]), output = apply(input, review(input), {max_rounds: 1});
    assert.equal(output.rows.length, 0);
  }
});
test('sibling and suffix-variant header locators cannot certify a target row', () => {
  for (const header of ['SUN2000L-5KTL', '5KTL', 'SUN2000L1-3.68KTL', 'SUN2000L-3.68KTL-M1']) {
    const input = {...task(), has_images: true};
    const output = apply(input, review(input, {checks: [check(input.rows[0], {evidence: proof(input.rows[0], {column_header: header})})]}), {max_rounds: 1});
    assert.equal(output.rows.length, 0, header);
  }
});
test('separate model header and series-shared header locators work', () => {
  for (const header of ['3.68KTL', 'SUN2000L']) {
    const input = task(), output = apply(input, review(input, {checks: [check(input.rows[0], {evidence: proof(input.rows[0], {column_header: header})})]}));
    assert.equal(output.confirmed_count, 1, header);
  }
});
test('compressed multi-model headers expand full identity for shared and model rows', () => {
  const examples = [
    [MODEL, 'SUN2000L-2/3/3.68/4/4.6/5KTL'],
    ['RX24V', 'RX12/24/48V'],
    ['CONV-10.5K-PLUS', 'CONV-7/10.5/12K-PLUS'],
    ['PVS-100-TL', 'PVS-80/100/120-TL'],
  ];
  for(const [model,header] of examples) for(const scope of ['model','shared']) {
    const item=row('compressed',{model,scope,applies_to_models:[model]});
    for(const imageOnly of [false,true]) {
      const input={...task([item]),model,plain_text:imageOnly?'':`${header}\nBattery voltage 350~450 Vdc`,has_images:imageOnly};
      const output=apply(input,review(input,{checks:[check(item,{evidence:proof(item,{column_header:header})})]}));
      assert.equal(output.confirmed_count,1,`${model} / ${header} / ${scope} / ${imageOnly}`);
      assert.equal(output.rows[0].model,model); assert.equal(output.rows[0].scope,scope);
    }
  }
});
test('compressed headers can locate a target whose original PDF lists separate full columns', () => {
  const input=task(), header='SUN2000L-2/3/3.68/4/4.6/5KTL';
  const output=apply(input,review(input,{checks:[check(input.rows[0],{evidence:proof(input.rows[0],{column_header:header})})]}));
  assert.equal(output.confirmed_count,1); assert.equal(output.rows[0].source_kind,'parser');
});
test('compressed headers never replace missing variants, near prefixes, or suffixes', () => {
  const headers=['SUN2000L-2/3/4/4.6/5KTL','SUN2000L-2/3/3.6/5KTL','SUN2000L1-2/3/3.68KTL',
    'SUN2000-2/3/3.68KTL','SUN2000L-2/3/3.68KTL-M1','SUN2000L-2/3/3.68K',
    'SUN2000L1-2KTL/3.68KTL'];
  for(const scope of ['shared','model']) for(const header of headers) {
    const item=row('bad-header',{scope}), input={...task([item]),has_images:true};
    const output=apply(input,review(input,{checks:[check(item,{evidence:proof(item,{column_header:header})})]}),{max_rounds:1});
    assert.equal(output.rows.length,0,scope+' / '+header);
  }
});
test('empty or invented locators do not confirm text-only rows', () => {
  for (const patch of [{raw_label: ''}, {raw_value: ''}, {column_header: ''}, {raw_label: 'An invented label'}, {raw_value: '300~450 Vdc'}]) {
    const input = task(), output = apply(input, review(input, {checks: [check(input.rows[0], {evidence: proof(input.rows[0], patch)})]}), {max_rounds: 1});
    assert.equal(output.rows.length, 0);
  }
});
test('repairable evidence metadata retains the original row ID without a false omission', () => {
  const input=task();
  const first=apply(input,review(input,{checks:[check(input.rows[0],{evidence:proof(input.rows[0],{page:5})})]}));
  assert.equal(first.done,false); assert.equal(first.rows.length,1); assert.equal(first.rows[0].row_id,'battery');
  assert.equal(first.row_states.battery.status,'pending'); assert.deepEqual(first.omissions,[]);
  assert.match(first.feedback,/exact PDF page/); assert.equal(first.history[0].action,'evidence_pending');
  const final=apply(first,review(first));
  assert.equal(final.done,true); assert.equal(final.confirmed_count,1); assert.equal(final.rows[0].row_id,'battery');
  assert.deepEqual(final.omissions,[]); assert.deepEqual(final.review_issues,[]);
});
test('a corrected value and full footnote survive a locator repair, including visual correction history', () => {
  for(const scanned of [false,true]){
    const original=row('battery',{value:'300~450'}),input={...task([original]),plain_text:scanned?'':text,has_images:scanned};
    const fixed={...original,value:'350~450',footnotes:['Only for PV strings. When using an LG battery, the maximum voltage is 495 V.']};
    const first=apply(input,review(input,{checks:[check(original,{outcome:'corrected',corrected_row:fixed,evidence:proof(fixed,{column_header:'Wrong header'}),reason:'The source lower bound is 350'})]}));
    assert.equal(first.done,false); assert.equal(first.rows[0].value,'350~450'); assert.deepEqual(first.rows[0].footnotes,fixed.footnotes);
    assert.equal(first.history[0].before.value,'300~450'); assert.equal(first.history[0].after.value,'350~450');
    const final=apply(first,review(first));
    assert.equal(final.confirmed_count,1); assert.deepEqual(final.omissions,[]);
    if(scanned){assert.equal(final.rows[0].correction.before.value,'300~450'); assert.equal(final.rows[0].correction.after.value,'350~450');}
    else assert.equal(final.rows[0].correction,undefined);
  }
});
test('repeated confirmation cannot promote an explicit wrong model after retries are exhausted', () => {
  let current=task([row('wrong',{model:'SUN2000L-5KTL'})]);
  for(let i=0;i<3;i++){
    current=apply(current,review(current));
    if(i<2){assert.equal(current.done,false); assert.equal(current.rows[0].model,'SUN2000L-5KTL'); assert.deepEqual(current.omissions,[]);}
  }
  assert.equal(current.done,true); assert.deepEqual(current.rows,[]); assert.equal(current.confirmed_count,0);
  assert.match(current.omissions.join(' '),/Unresolved evidence metadata: Parameter page or model/);
});
test('resolved legacy metadata omissions migrate by original page, model and label while keeping history', () => {
  const reason='Evidence header does not locate the target model or its explicitly shared series';
  const old=row('old-battery',{value:'300~450'}), oldRecord={round:1,revision:0,row_id:old.row_id,action:'omitted',before:old,evidence:proof(old),reason};
  const input={...task([row('recovered-battery')]),round:1,history:[oldRecord],omissions:[old.row_id+': '+reason,'emc: No EMC row exists','unknown: Column unclear']};
  const output=apply(input,review(input));
  assert.equal(output.done,true); assert.equal(output.confirmed_count,1);
  assert.deepEqual(output.omissions,['emc: No EMC row exists','unknown: Column unclear']);
  assert.deepEqual(output.history[0],oldRecord); assert.equal(output.history.at(-1).action,'evidence_issue_resolved');
  assert.equal(output.history.at(-1).resolved_by_row_id,'recovered-battery');
});
test('legacy cleanup never clears mismatched identity, unrelated reasons, or explicit content omissions', () => {
  const technical='Evidence header does not locate the target model or its explicitly shared series';
  for(const patch of [{before:row('old',{page:5})},{before:row('old',{model:'SUN2000L-5KTL'})},
    {before:row('old',{source_label:'AC short-circuit protection'})},{reason:'No source supports this parameter'},
    {review_outcome:'unsupported'},{review_outcome:'uncertain'}]){
    const entry={round:1,revision:0,row_id:'old',action:'omitted',before:row('old'),reason:technical,...patch};
    const omission='old: '+entry.reason,input={...task(),round:1,history:[entry],omissions:[omission]};
    const output=apply(input,review(input));
    assert.deepEqual(output.omissions,[omission]);
  }
});
test('a real value locator cannot validate a fabricated parameter label', () => {
  const input = task([row('fake', {source_label: 'Imaginary operating limit'})]);
  const output = apply(input, review(input, {checks: [check(input.rows[0], {evidence: proof(input.rows[0], {raw_label: 'Battery voltage'})})]}), {max_rounds: 1});
  assert.equal(output.rows.length, 0);
});
test('numeric substring, lost sign and decimal mismatches are not accepted', () => {
  for (const [value, raw] of [['1', '11'], ['350', '1350'], ['350', '-350'], ['98', '98.5']]) {
    const input = {...task([row('bad', {value})]), has_images: true};
    const output = apply(input, review(input, {checks: [check(input.rows[0], {evidence: proof(input.rows[0], {raw_value: raw})})]}), {max_rounds: 1});
    assert.equal(output.rows.length, 0, `${value} in ${raw}`);
  }
});
test('valid thousands grouping compares without rewriting raw values or evidence', () => {
  for(const [value,quoted] of [['5520','5,520'],['5,520','5520'],['1234567.5','1,234,567.5'],['-5520','-5,520'],['-5520~+6000','-5,520~+6,000']]) {
    const item=row('power',{source_label:'Recommended maximum power',value,unit:'Wp'});
    const input={...task([item]),plain_text:`${MODEL}\nRecommended maximum power ${value} Wp`};
    const audit=review(input,{checks:[check(item,{evidence:proof(item,{raw_value:quoted+' Wp'})})]});
    const output=apply(input,audit);
    assert.equal(output.confirmed_count,1,`${value} / ${quoted}`);
    assert.equal(output.rows[0].value,value); assert.equal(output.row_states.power.evidence.raw_value,quoted+' Wp');
  }
});
test('thousands comparison never removes decimal commas, signs, or malformed grouping', () => {
  for(const [value,quoted] of [['8','0,8'],['08','0,8'],['800','0,800'],['0.8','0,8'],['5520','-5,520'],['5520','55,20'],['1234567','12,34,567'],['5520-6000','5,520+6,000']]) {
    const item=row('bad-number',{value}), input={...task([item]),has_images:true};
    const output=apply(input,review(input,{checks:[check(item,{evidence:proof(item,{raw_value:quoted})})]}),{max_rounds:1});
    assert.equal(output.rows.length,0,`${value} / ${quoted}`);
  }
  const item=row('decimal',{value:'0,8'}),input={...task([item]),has_images:true};
  assert.equal(apply(input,review(input)).confirmed_count,1,'An unchanged decimal comma stays valid');
});
test('PDF whitespace and repeated numeric cells are not treated as one larger number', () => {
  const input = task([row('mppt', {source_label: 'Maximum MPPT current', value: 11, unit: 'A'})]);
  const output = apply(input, review(input)); assert.equal(output.confirmed_count, 1);
  const spaced = {...task(), plain_text: text.replace('350~450', '350 ~\n 450')};
  assert.equal(apply(spaced, review(spaced)).confirmed_count, 1);
});
test('scanned pages need actual image availability; page render receipts survive', () => {
  const noImage = {...task(), plain_text: ''};
  assert.equal(apply(noImage, review(noImage), {max_rounds: 1}).rows.length, 0);
  const image = {...noImage, has_images: true, render_receipts: [{signed: 'page-4'}]};
  const output = apply(image, review(image));
  assert.equal(output.confirmed_count, 1); assert.equal(output.rows[0].source_kind, 'visual');
  assert.deepEqual(output.render_receipts, image.render_receipts);
});
test('visual corrections keep before/after/reason and recheck before acceptance', () => {
  const input = {...task([row('battery', {value: '300~450'})]), plain_text: '', has_images: true};
  const output = apply(input, review(input, {checks: [check(input.rows[0], {outcome: 'corrected', corrected_row: {value: '350~450'}, evidence: proof(row()), reason: 'The enlarged cell reads 350'})]}));
  assert.equal(output.done, false); assert.equal(output.rows[0].source_kind, 'visual');
  assert.deepEqual(output.rows[0].correction, {before: {value: '300~450', unit: 'Vdc'}, after: {value: '350~450', unit: 'Vdc'}, reason: 'The enlarged cell reads 350'});
  const final = apply(output, review(output));
  assert.equal(final.confirmed_count, 1); assert.deepEqual(final.rows[0].correction, output.rows[0].correction);
});
test('text-only verification does not forge a visual correction receipt', () => {
  const input = task([row('battery', {source_kind: 'visual', correction: {before: {value: '300~450'}, after: {value: '350~450'}, reason: 'Earlier visual draft'}})]);
  const output = apply(input, review(input));
  assert.equal(output.rows[0].source_kind, 'parser'); assert.equal(output.rows[0].correction, undefined);
});
test('last-round corrected and new rows are omitted while confirmed rows survive', () => {
  const input = {...task([row('battery', {value: '300~450'}), row('efficiency', {source_label: 'Maximum efficiency', value: '98.5', unit: '%'})]), round: 2};
  const addition = row('harmonics', {source_label: 'Maximum total harmonic distortion', value: '≤3', unit: '%'});
  const output = apply(input, review(input, {checks: [check(input.rows[0], {outcome: 'corrected', corrected_row: {value: '350~450'}, evidence: proof(row()), reason: 'Correct lower bound'}), check(input.rows[1])], additions: [{...addition, evidence: proof(addition)}]}));
  assert.equal(output.done, true); assert.equal(output.confirmed_count, 1);
  assert.deepEqual(output.rows.map(item => item.row_id), ['efficiency']);
  assert.equal(output.omissions.filter(item => item.includes('independent recheck')).length, 2);
  assert.deepEqual(Object.keys(output.row_states), ['efficiency']);
  assert.equal(output.history.filter(entry=>entry.action==='unreviewed_change_omitted').length,2);
});
test('every successful final review emits only confirmed ledger rows for all retry budgets', () => {
  for (const maxRounds of [1, 2, 3]) for (const complete of [true, false]) {
    const input = {...task([row('battery', {value: '300~450'}), row('efficiency', {source_label: 'Maximum efficiency', value: '98.5', unit: '%'})]), round: maxRounds - 1};
    const addition = row('harmonics', {source_label: 'Maximum total harmonic distortion', value: '≤3', unit: '%'});
    const output = apply(input, review(input, {checks: [check(input.rows[0], {outcome: 'corrected', corrected_row: {value: '350~450'}, evidence: proof(row()), reason: 'Correct lower bound'}), check(input.rows[1])], additions: [{...addition, evidence: proof(addition)}], coverage: {complete, missing: complete ? [] : ['More rows may remain']}}), {max_rounds: maxRounds});
    assert.equal(output.done,true); assert.equal(output.failed,false); assert.equal(output.rows.length,1);
    assert.equal(output.confirmed_count,output.rows.length); assert.equal(output.rows[0].row_id,'efficiency');
    assert.ok(output.rows.every(item=>output.row_states[item.row_id]?.status==='confirmed'));
    assert.ok(output.history.some(entry=>entry.action==='corrected_pending'),'The corrected candidate reached the common final gate');
    assert.ok(output.history.some(entry=>entry.action==='added_pending'),'The added candidate reached the common final gate');
    assert.equal(output.history.filter(entry=>entry.action==='unreviewed_change_omitted').length,2);
  }
});
test('a valid last review containing only corrections and additions yields no publishable rows', () => {
  const input = {...task([row('battery', {value: '300~450'})]), round: 2};
  const addition = row('efficiency', {source_label: 'Maximum efficiency', value: '98.5', unit: '%'});
  const output = apply(input, review(input, {checks: [check(input.rows[0], {outcome: 'corrected', corrected_row: {value: '350~450'}, evidence: proof(row()), reason: 'Correct lower bound'})], additions: [{...addition,evidence:proof(addition)}]}));
  assert.equal(output.done,true); assert.equal(output.failed,false); assert.deepEqual(output.rows,[]);
  assert.equal(output.confirmed_count,0); assert.deepEqual(Object.keys(output.row_states),[]);
  assert.equal(output.omissions.filter(item=>item.includes('independent recheck')).length,2);
});
test('coverage gaps and crop requests drive another pass without losing confirmed rows', () => {
  const input = task(), output = apply(input, review(input, {coverage: {complete: false, missing: ['AC short-circuit protection']}, needs_zoom: true, region: [0.1, 0.2, 0.9, 0.8]}));
  assert.equal(output.done, false); assert.equal(output.rows.length, 1); assert.equal(output.confirmed_count, 1);
  assert.deepEqual(output.region, [0.1, 0.2, 0.9, 0.8]); assert.equal(output.revision, 0);
});
test('coverage gaps become explicit omissions after the retry limit', () => {
  const input = {...task(), round: 2};
  const output = apply(input, review(input, {coverage: {complete: false, missing: ['Unreadable footnote']}, needs_zoom: true, region: [0, 0, 1, 1]}));
  assert.equal(output.done, true); assert.equal(output.rows.length, 1);
  assert.match(output.omissions.join(' '), /Unreadable footnote/);
});
test('invalid crop coordinates are ignored', () => {
  for (const region of [[1, 0, 0, 1], [-1, 0, 1, 1], [0, 0, 1], ['0', 0, 1, 1]]) {
    const input = task(), output = apply(input, review(input, {needs_zoom: true, region}));
    assert.equal(output.region, null);
  }
});
test('malformed patches fail atomically before approving an earlier row', () => {
  const input = task([row(), row('second')]);
  const output = apply(input, review(input, {checks: [check(input.rows[0]), check(input.rows[1], {outcome: 'corrected', corrected_row: null})]}));
  assert.equal(output.failed, true); assert.equal(output.confirmed_count, 0); assert.deepEqual(output.rows, input.rows);
});
test('final malformed review preserves previously confirmed unchanged rows, but drops pending additions', () => {
  const input = task(), addition = row('new', {source_label: 'Maximum efficiency', value: '98.5', unit: '%'});
  const first = apply(input, review(input, {additions: [{...addition, evidence: proof(addition)}]}));
  const final = apply(first, review(first, {checks: []}), {max_rounds: 2});
  assert.equal(final.done, true); assert.equal(final.failed, true); assert.equal(final.confirmed_count, 1);
  assert.deepEqual(final.rows.map(item => item.row_id), ['battery']);
  assert.equal(final.row_states.battery.status, 'confirmed');
  assert.ok(final.omissions.some(value => value.includes('not confirmed by the final audit')));
});
test('final malformed review does not trust a row_state without the actual prior confirmation history', () => {
  const input = {...task(), round: 2, row_states: {battery: {status: 'confirmed', reviewed_revision: 0, evidence: proof(row())}}};
  const final = apply(input, review(input, {checks: []}));
  assert.equal(final.confirmed_count, 0); assert.deepEqual(final.rows, []);
});
test('final malformed review rejects edited rows, edited evidence and pending corrections', () => {
  for (const edit of [
    value => { value.rows[0].value = '300~450'; },
    value => { value.row_states.battery.evidence.raw_value = '300~450 Vdc'; },
    value => { value.row_states.battery.status = 'pending'; },
  ]) {
    const input = task(), first = apply(input, review(input, {coverage: {complete: false, missing: ['Footnote']}}));
    edit(first);
    const final = apply(first, review(first, {checks: []}), {max_rounds: 2});
    assert.equal(final.done, true); assert.equal(final.confirmed_count, 0); assert.deepEqual(final.rows, []);
  }
});
test('preserved confirmation matches row data independently of JSON object key order', () => {
  const input = task(), first = apply(input, review(input, {coverage: {complete: false, missing: ['Footnote']}}));
  first.rows[0] = Object.fromEntries(Object.entries(first.rows[0]).reverse());
  const final = apply(first, review(first, {checks: []}), {max_rounds: 2});
  assert.equal(final.confirmed_count, 1); assert.equal(final.rows[0].value, '350~450');
});
test('data-like prototype keys cannot mutate the review state prototype', () => {
  const input = task([row('__proto__')]), output = apply(input, review(input));
  assert.equal(output.confirmed_count, 1); assert.equal(Object.getPrototypeOf(output.row_states), null);
  assert.equal(output.row_states.__proto__.status, 'confirmed');
});
test('350 rows survive complete review without trimming or rewriting', () => {
  const input = task(Array.from({length: 350}, (_, index) => row(`row-${index}`, {conditions: `Condition ${index}`})));
  const output = apply(input, review(input));
  assert.equal(output.done, true); assert.equal(output.confirmed_count, 350); assert.equal(output.rows.length, 350);
  assert.deepEqual(output.rows, input.rows);
});
test('continued-page model labels can identify a column without supplying parameter facts', () => {
  const input = {...task(), plain_text: 'Battery voltage 350~450 Vdc', header_text: MODEL};
  const output = apply(input, review(input));
  assert.equal(output.confirmed_count, 1);
  assert.equal(output.row_states.battery.basis, 'text');
});
test('reference header context never proves a value missing from current-page text', () => {
  const input = {...task(), plain_text: 'Battery voltage is listed elsewhere', header_text: text};
  const output = apply(input, review(input));
  assert.equal(output.confirmed_count, 0);
  assert.ok(output.review_issues.some(issue=>JSON.stringify(issue).includes('Locators are not supported')));
});
console.log(JSON.stringify({ok: true, tests, module: 'closed_loop_review'}));
