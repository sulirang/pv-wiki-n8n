'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const workflow=JSON.parse(fs.readFileSync(path.join(__dirname,'pv-wiki-datasheet-agent.json'),'utf8'));
const nodes=Object.fromEntries(workflow.nodes.map(n=>[n.name,n]));
function run(name,value,refs={}){
 const values=Array.isArray(value)?value:[value];
 const input={first:()=>({json:values[0]}),all:()=>values.map(json=>({json}))};
 const lookup=name=>{if(!(name in refs))throw new Error('Missing reference '+name);const v=refs[name];return{first:()=>v.json?v:{json:v},last:()=>v.json?v:{json:v}};};
 return new Function('$input','$',nodes[name].parameters.jsCode)(input,lookup);
}
for(const n of workflow.nodes.filter(n=>n.type==='n8n-nodes-base.code'))new Function('$input','$',n.parameters.jsCode);
const selected={product_id:'SKU',source_hash:'source',revision:'new:source',product:{brand_code:'Maker',product_name:'M-500'}};
const config={vision_enabled:true,max_review_rounds:3,save_results:true};
const claim={work:{kind:'research',product:selected},publication_configured:false};
let state=run('Initialize Research',{publication_configured:false},{'Claim One Item':claim,'Configuration':config})[0].json;
assert.equal(state.next_action,'discover');
assert.deepEqual(run('Configuration',{})[0].json.datasheet_hints,{},'The distributable workflow must not hard-code one product source');
const hintConfig={...config,datasheet_hints:{SKU:['https://maker.example/exact-en.pdf',' https://maker.example/exact-en.pdf ',null,'not-a-url'],OTHER:['https://other.example/private-product.pdf']}};
const hinted=run('Initialize Research',{publication_configured:false},{'Claim One Item':claim,'Configuration':hintConfig})[0].json;
const hintPrompt=run('Discovery Input',hinted)[0].json.discovery_prompt;
assert.deepEqual(hintPrompt.known_datasheet_urls,['https://maker.example/exact-en.pdf']);
assert.equal(JSON.stringify(hintPrompt).includes('other.example'),false,'One product must never receive another product\'s hints');
const otherClaim={...claim,work:{...claim.work,product:{...selected,product_id:'OTHER'}}};
const otherHinted=run('Initialize Research',{publication_configured:false},{'Claim One Item':otherClaim,'Configuration':hintConfig})[0].json;
assert.deepEqual(run('Discovery Input',otherHinted)[0].json.discovery_prompt.known_datasheet_urls,['https://other.example/private-product.pdf']);
const unhinted=run('Initialize Research',{publication_configured:false},{'Claim One Item':claim,'Configuration':{...config,datasheet_hints:{OTHER:['https://other.example/private-product.pdf']}}})[0].json;
assert.deepEqual(run('Discovery Input',unhinted)[0].json.discovery_prompt.known_datasheet_urls,[]);
const restoredHints=run('Initialize Research',{publication_configured:false},{'Claim One Item':{...claim,work:{...claim.work,checkpoint:{context:hinted}}},'Configuration':config})[0].json;
assert.deepEqual(run('Discovery Input',restoredHints)[0].json.discovery_prompt.known_datasheet_urls,[],'Recovery must refresh hints from current configuration, not reuse another claim\'s old hints');
state=run('Discovery Input',state)[0].json;
assert.equal(state.discovery_prompt.preferred_datasheet_language,'en');
const languageReason='Official English search returned only a different generation; this Dutch document explicitly covers M-500.';
state=run('Read Discovery',{output:JSON.stringify({found:true,manufacturer:'Maker',target_model:'M-500',url:'https://maker.example/pdf',verification_reason:'Official source',datasheet_language:'nl',language_selection_reason:languageReason})},{'Discovery Input':state})[0].json;
assert.equal(state.found,true);
assert.equal(state.discovery.datasheet_language,'nl');
assert.equal(state.discovery.language_selection_reason,languageReason);
const olderDiscovery=run('Read Discovery',{output:JSON.stringify({found:true,manufacturer:'Maker',target_model:'M-500',url:'https://maker.example/en/download'})},{'Discovery Input':state})[0].json;
assert.equal(olderDiscovery.discovery.datasheet_language,'unknown','An English-looking path does not establish source language');
const huaweiSelected={...selected,manufacturer_hint:'Huawei',product:{brand_code:'HUAWEI',product_name:'HUAWEI SUN2000L - 3.68KTL'}};
const huaweiClaim={...claim,work:{...claim.work,product:huaweiSelected}};
const modelCases=[
 ['HUAWEI SUN2000L - 3.68KTL','Huawei','Huawei','HUAWEI','SUN2000L-3.68KTL'],
 ['Huawei: SUN2000-3KTL-L1','Huawei','Huawei','HUAWEI','SUN2000-3KTL-L1'],
 ['huawei-SUN2000L-3.68KTL-LA','Huawei','Huawei','HUAWEI','SUN2000L-3.68KTL-LA'],
 ['Huawei — SUN2000L - 3.68KTL','Huawei','Huawei','HUAWEI','SUN2000L-3.68KTL'],
 ['SMA Solar Technology - STP 10000TL - 20','SMA','SMA Solar Technology','SMA','STP 10000TL-20'],
 ['SolarEdge SE5000H-US','SolarEdge','SolarEdge','SE','SE5000H-US'],
 ['HUAWEI2-SUN2000L-3.68KTL','Huawei','Huawei','HUAWEI','HUAWEI2-SUN2000L-3.68KTL'],
 ['HUAWEISUN2000L-3.68KTL','Huawei','Huawei','HUAWEI','HUAWEISUN2000L-3.68KTL'],
 ['UNKNOWN SUN2000L-3.68KTL','Huawei','Huawei','HUAWEI','UNKNOWN SUN2000L-3.68KTL'],
 ['SUN2000L - 3.68KTL','Huawei','Huawei','HUAWEI','SUN2000L-3.68KTL'],
];
for(const [reported,hint,manufacturer,brand,expected] of modelCases){
 const selectedModel={...selected,manufacturer_hint:hint,product:{brand_code:brand,product_name:reported}};
 const before=JSON.stringify(selectedModel);
 const discovered=run('Read Discovery',{output:JSON.stringify({found:true,manufacturer,target_model:reported,url:'https://maker.example/pdf'})},{'Discovery Input':{...state,selected:selectedModel}})[0].json;
 assert.equal(discovered.discovery.target_model,expected,reported);
 assert.equal(JSON.stringify(discovered.selected),before,'Model normalization must not modify the catalogue product title or selected revision');
}
const huaweiInitial=run('Initialize Research',{publication_configured:false},{'Claim One Item':huaweiClaim,'Configuration':config})[0].json;
let normalizedDraft=run('Read Discovery',{output:JSON.stringify({found:true,manufacturer:'Huawei',target_model:'HUAWEI SUN2000L - 3.68KTL',url:'https://maker.example/pdf'})},{'Discovery Input':huaweiInitial})[0].json;
normalizedDraft.current={page:2,header_page:2,model:normalizedDraft.discovery.target_model,plain_text:'SUN2000L-3.68KTL\nRated power 3,680 W',layout_text:'',rows:[],round:0,revision:0,omissions:[],history:[],render_receipts:[],has_images:false,extracted:false};
normalizedDraft=run('Read Page Draft',{text:JSON.stringify({applicability:'target',parameters:[{source_label:'Rated power',value:'3,680',unit:'W',model:'SUN2000L-3.68KTL',scope:'model'}]})},{'Choose Page':normalizedDraft})[0].json;
assert.equal(normalizedDraft.current.rows.length,1,'An exact source-model row survives catalogue-brand normalization');
const normalizedAudit=run('Prepare Audit',normalizedDraft)[0].json;
const normalizedReviewed=run('Apply Page Audit',{text:JSON.stringify({page:2,revision:0,applicability:'target',checks:[{row_id:normalizedDraft.current.rows[0].row_id,outcome:'confirmed',evidence:{page:2,raw_label:'Rated power',raw_value:'3,680 W',column_header:'SUN2000L-3.68KTL',footnotes:[]}}],additions:[],coverage:{complete:true,missing:[]},omissions:[]})},{'Prepare Audit':normalizedAudit})[0].json;
assert.equal(normalizedReviewed.next_action,'page_done','The actual manufacturer model header must pass the independent core audit');
assert.equal(normalizedReviewed.current.confirmed_count,1);
const goodModelCheckpoint={...normalizedReviewed,pdf:{document_id:'source-pdf',target_model:'SUN2000L-3.68KTL',receipt:{target_model:'SUN2000L-3.68KTL',signature:'fixture'}},page_plan:[{page:2,header_page:2,applicability:'review'}],reviewed_pages:[{page:1,rows:[]}]};
for(const mutation of [
 c=>{c.discovery.target_model='HUAWEI SUN2000L - 3.68KTL';c.pdf.target_model=c.discovery.target_model;c.pdf.receipt.target_model=c.discovery.target_model;},
 c=>{c.pdf.target_model='SUN2000L-5KTL';},
 c=>{c.pdf.receipt.target_model='SUN2000L-5KTL';},
 c=>{delete c.pdf.target_model;},
 c=>{delete c.pdf.receipt.target_model;},
]){
 const oldCheckpoint=structuredClone(goodModelCheckpoint);mutation(oldCheckpoint);
 const oldBefore=JSON.stringify(oldCheckpoint);
 const reset=run('Initialize Research',{publication_configured:false},{'Claim One Item':{...huaweiClaim,work:{...huaweiClaim.work,checkpoint:{context:oldCheckpoint}}},'Configuration':config})[0].json;
 assert.equal(reset.next_action,'discover');assert.equal(reset.resumed,false);
 assert.equal(reset.pdf,null);assert.equal(reset.discovery,null);assert.equal(reset.current,null);
 assert.deepEqual(reset.page_plan,[]);assert.deepEqual(reset.reviewed_pages,[]);
 assert.deepEqual(reset.selected,huaweiSelected,'Discarding an incompatible receipt does not change the catalogue or source revision');
 assert.match(reset.checkpoint_discarded_reason,/receipt/);
 assert.equal(JSON.stringify(oldCheckpoint),oldBefore,'The old checkpoint is read-only; rebuilding happens in a fresh state');
}
const goodResume=run('Initialize Research',{publication_configured:false},{'Claim One Item':{...huaweiClaim,work:{...huaweiClaim.work,checkpoint:{context:goodModelCheckpoint}}},'Configuration':config})[0].json;
assert.equal(goodResume.resumed,true);assert.equal(goodResume.next_action,'plan');
assert.deepEqual(goodResume.pdf,goodModelCheckpoint.pdf);
assert.equal(JSON.stringify(goodResume.current),JSON.stringify(goodModelCheckpoint.current));
const pdf={ok:true,document_id:'a'.repeat(64),artifact_sha256:'a'.repeat(64),target_model:'M-500',receipt:{signed:'source',target_model:'M-500'},url:'https://maker.example/pdf',content:'full signed PDF',page_count:3,page_texts:[{page:1,plain_text:'Cover'},{page:2,plain_text:'M-500\nBattery voltage 350 V\nStartup 120 V',layout_text:'M-500  Battery voltage  350 V'},{page:3,plain_text:'Different optimizer M-50'}]};
state=run('Read PDF Result',pdf,{'Read Discovery':state})[0].json;
assert.equal(state.next_action,'plan');
state=run('Prepare Page Plan',state)[0].json;
state=run('Read Page Plan',{text:JSON.stringify({pages:[{page:1,applicability:'unrelated'},{page:2,applicability:'unrelated',header_page:2},{page:3,applicability:'unrelated'}]})},{'Prepare Page Plan':state})[0].json;
assert.equal(state.page_plan[1].applicability,'review','Explicit target page cannot be silently excluded');
state=run('Choose Page',state)[0].json;
assert.equal(state.current.page,2);
const choice=structuredClone(state);
state=run('Read Page Draft',{text:JSON.stringify({applicability:'target',parameters:[{source_label:'Battery voltage',value:'300',unit:'V',model:'M-500',scope:'model',section:'Battery'}]})},{'Choose Page':state})[0].json;
assert.equal(state.current.rows.length,1);
assert.equal(state.next_action,'audit');
const firstId=state.current.rows[0].row_id;
state=run('Prepare Audit',state)[0].json;
const rendered={images:[{page:2,data:Buffer.from('real-image-fixture').toString('base64'),mime_type:'image/png'}],receipt:{signed:'view'}};
const attached=run('Attach Audit Images',rendered,{'Prepare Audit':state})[0];
assert.equal(attached.json.current.has_images,true);
assert.equal(Buffer.from(attached.binary.page.data,'base64').toString(),'real-image-fixture');
const audit=(revision,checks,additions=[])=>({page:2,revision,applicability:'target',checks,additions,coverage:{complete:true,missing:[]},omissions:[],needs_zoom:false,region:null});
const evidence=(label,value)=>({page:2,raw_label:label,raw_value:value,column_header:'M-500',footnotes:[]});
for(const message of ['Expected double-quoted property name in JSON at position 542 (line 10 column 3)','Unexpected end of JSON input']){
 for(const error of [message,{message}]){
  const before=JSON.stringify(state);
  const malformed=run('Apply Page Audit',{error},{'Prepare Audit':state})[0].json;
  assert.equal(malformed.next_action,'audit',message+' must return to the bounded repair loop');
  assert.equal(malformed.error,null);
  assert.equal(malformed.current.round,state.current.round+1);
  assert.equal(malformed.current.revision,state.current.revision);
  assert.equal(malformed.current.done,false);
  assert.deepEqual(malformed.current.rows,state.current.rows,'Malformed JSON must preserve the current candidates');
  assert.match(malformed.current.feedback,/complete compact JSON object/);
  assert.ok(malformed.current.feedback.includes(message));
  assert.equal(JSON.stringify(state),before,'The failed model response cannot mutate the input checkpoint');
  const repairInput=run('Prepare Audit',malformed)[0].json;
  assert.ok(repairInput.audit_prompt.previous_feedback.includes(message),'The next model call receives the actual JSON formatting error');
  const repaired=run('Apply Page Audit',{text:JSON.stringify(audit(repairInput.current.revision,[{row_id:firstId,outcome:'corrected',corrected_row:{value:'350'},evidence:evidence('Battery voltage','350'),reason:'Source reads 350'}]))},{'Prepare Audit':repairInput})[0].json;
  assert.equal(repaired.next_action,'audit','Recovering from JSON failure does not bypass independent recheck');
  const recheckInput=run('Prepare Audit',repaired)[0].json;
  const confirmed=run('Apply Page Audit',{text:JSON.stringify(audit(recheckInput.current.revision,[{row_id:firstId,outcome:'confirmed',evidence:evidence('Battery voltage','350'),reason:'Matches source'}]))},{'Prepare Audit':recheckInput})[0].json;
  assert.equal(confirmed.next_action,'page_done');
  assert.equal(confirmed.current.confirmed_count,1);
  assert.equal(confirmed.current.rows[0].value,'350');
 }
}
for(const message of ['Request timed out','HTTP 504 Gateway Timeout','terminated','other side closed','connect ECONNRESET']){
 for(const error of [message,{message}]){
  const interrupted=run('Apply Page Audit',{error},{'Prepare Audit':state})[0].json;
  assert.equal(interrupted.next_action,'stop',message+' must end this research attempt');
  assert.deepEqual(interrupted.error,{stage:'audit',page:2,message});
  assert.equal(interrupted.current.round,state.current.round,'Transport failures must not be consumed as content repair attempts');
  assert.equal(interrupted.current.revision,state.current.revision);
  assert.deepEqual(interrupted.current.rows,state.current.rows);
  assert.deepEqual(interrupted.current.history,state.current.history);
 }
}
state=run('Apply Page Audit',{text:JSON.stringify(audit(0,[{row_id:firstId,outcome:'corrected',corrected_row:{value:'350'},evidence:evidence('Battery voltage','350'),reason:'Source reads 350'}]))},{'Prepare Audit':state,'Attach Audit Images':attached})[0].json;
assert.equal(state.current.rows[0].value,'350');
assert.equal(state.next_action,'audit','Changed rows must be independently rechecked');
assert.equal(state.current.revision,1);
state=run('Progress Snapshot',state)[0].json;
const checkpoint=structuredClone(state);
state=run('Restore Progress',{ok:true},{'Progress Snapshot':state})[0].json;
state=run('Prepare Audit',state)[0].json;
state=run('Apply Page Audit',{text:JSON.stringify(audit(1,[{row_id:firstId,outcome:'confirmed',evidence:evidence('Battery voltage','350'),reason:'Matches'}]))},{'Prepare Audit':state})[0].json;
assert.equal(state.next_action,'page_done');
const finishedCheckpoint=run('Progress Snapshot',state)[0].json;
const finishedResume=run('Initialize Research',{publication_configured:false},{'Claim One Item':{...claim,work:{...claim.work,checkpoint:{context:finishedCheckpoint,stage:'draft'}}},'Configuration':config})[0].json;
const finishedPlan=run('Prepare Page Plan',finishedResume)[0].json;
assert.equal(finishedPlan.plan_ready,true);
const finishedChoice=run('Choose Page',finishedPlan)[0].json;
assert.equal(finishedChoice.has_page,false,'A checkpoint after final audit must bypass another extraction/audit model call');
assert.equal(finishedChoice.current,null);
assert.equal(finishedChoice.reviewed_pages.filter(p=>p.page===2).length,1);
assert.deepEqual(finishedChoice.reviewed_pages.find(p=>p.page===2).rows,finishedCheckpoint.current.rows);
assert.deepEqual(finishedChoice.reviewed_pages.find(p=>p.page===2).history,finishedCheckpoint.current.history);
const finishedAgain=run('Choose Page',finishedChoice)[0].json;
assert.equal(finishedAgain.page_cursor,finishedChoice.page_cursor);
assert.equal(finishedAgain.reviewed_pages.filter(p=>p.page===2).length,1,'Promoting a completed checkpoint is idempotent');
const nextPageResume=structuredClone(finishedPlan);
nextPageResume.page_plan[2].applicability='review';
const nextPageChoice=run('Choose Page',nextPageResume)[0].json;
assert.equal(nextPageChoice.has_page,true);
assert.equal(nextPageChoice.current.page,3,'A completed page resumes at the next eligible page');
assert.equal(nextPageChoice.extraction_needed,true);
state=run('Finish Page',state)[0].json;
state=run('Choose Page',state)[0].json;
assert.equal(state.has_page,false);
state=run('Build Decision',state)[0].json;
assert.equal(state.decision.parameters[0].value,'350');
assert.equal(state.decision.expected_revision,selected.revision);
assert.equal(state.decision.parameters[0].parameter_id,'p001');
assert.deepEqual(state.evidence_documents[0].receipt,pdf.receipt);
assert.equal(state.decision.official_source.datasheet_language,'nl');
assert.equal(state.decision.official_source.language_selection_reason,languageReason);
const resumed=run('Initialize Research',{publication_configured:false},{'Claim One Item':{...claim,work:{...claim.work,checkpoint:{context:checkpoint,stage:'draft'}}},'Configuration':config})[0].json;
assert.equal(resumed.current.rows[0].value,'350');
assert.equal(resumed.current.revision,1);
assert.equal(resumed.next_action,'plan');
const disconnected=run('Read Page Draft',{error:'terminated'},{'Choose Page':choice})[0].json;
assert.equal(disconnected.next_action,'stop');
const many=structuredClone(state);
many.decision.parameters=Array.from({length:350},(_,i)=>({...state.decision.parameters[0],parameter_id:'p'+String(i+1).padStart(3,'0')}));
const batches=run('Prepare Translation Batches',many);
assert.equal(batches.length,14);
assert.equal(batches.flatMap(i=>i.json.parameters).length,350);
assert.ok(batches.every(batch=>batch.json.datasheet_language==='nl'));
const bilingual=structuredClone(many);
bilingual.decision.parameters=bilingual.decision.parameters.map((row,i)=>({...row,
 value:i%3===0?'Ja':i%3===1?'0.8 geleidend ... 0.8 na-ijlend':'3,680 W',
 conditions:'Bij 25 °C; alleen met LG Chem RESU 7H_R / 10H_R',
 footnotes:['Maximale spanning 495 V bij gebruik van de LG batterij.','EN/IEC 62109-1, EN/IEC 62109-2']
}));
const bilingualBefore=JSON.stringify(bilingual.decision.parameters);
const bilingualBatches=run('Prepare Translation Batches',bilingual);
assert.equal(bilingualBatches.flatMap(item=>item.json.parameters).length,350);
const batchResponses=bilingualBatches.map(item=>({text:JSON.stringify({analysis:{
 translations:item.json.parameters.map(row=>({parameter_id:row.parameter_id,name_zh:'参数',section_zh:'输入',
  ...(row.value==='Ja'?{value_zh:'支持'}:row.value.includes('geleidend')?{value_zh:'0.8 超前 ... 0.8 滞后'}:{}),
  conditions_zh:'25 °C 时；仅适用于 LG Chem RESU 7H_R / 10H_R',
  footnotes_zh:['使用 LG 电池时，最大电压为 495 V。','EN/IEC 62109-1, EN/IEC 62109-2']
 })),sections:[],overall_limitations_zh:[]}})}));
const bilingualMerged=run('Merge Translations',batchResponses,{'Build Decision':bilingual})[0].json;
assert.equal(bilingualMerged.proposed_translations.translations.length,350);
const bilingualAudited=run('Read Translation Audit',{text:JSON.stringify({approved:true,analysis:bilingualMerged.proposed_translations})},{'Merge Translations':bilingualMerged})[0].json;
assert.equal(bilingualAudited.decision.analysis.translations.length,350,'Bilingual enrichment must not cap the final row count');
assert.deepEqual(Object.keys(bilingualMerged.proposed_translations),['translations']);
assert.deepEqual(Object.keys(bilingualAudited.decision.analysis),['translations']);
assert.equal(JSON.stringify(bilingualAudited.decision.parameters),bilingualBefore,'Display translations never replace raw source values, conditions or footnotes');
const unsolicitedAnalysis={sections:[{section_code:'product_positioning',paragraphs:[{analysis_zh:'Recommended for every project'}]}],overall_limitations_zh:['Comparison should be generated later'],recommendations:['Buy this one']};
const translationOnlyMerged=run('Merge Translations',[{text:JSON.stringify({translations:bilingualMerged.proposed_translations.translations,...unsolicitedAnalysis})}],{'Build Decision':bilingual})[0].json;
assert.deepEqual(translationOnlyMerged.proposed_translations,bilingualMerged.proposed_translations,'Unrequested analysis must not enter translation review');
const translationOnlyAudited=run('Read Translation Audit',{text:JSON.stringify({approved:true,translations:translationOnlyMerged.proposed_translations.translations,...unsolicitedAnalysis})},{'Merge Translations':translationOnlyMerged})[0].json;
assert.deepEqual(translationOnlyAudited.decision.analysis,bilingualAudited.decision.analysis,'Unrequested analysis must not enter the saved decision');
const legacySpillAudited=run('Read Translation Audit',{text:JSON.stringify({approved:true,analysis:{translations:bilingualMerged.proposed_translations.translations,...unsolicitedAnalysis}})},{'Merge Translations':bilingualMerged})[0].json;
assert.deepEqual(legacySpillAudited.decision.analysis,bilingualAudited.decision.analysis,'A legacy response wrapper cannot reintroduce analysis');
const noTranslationAudit=run('Read Translation Audit',{text:JSON.stringify({approved:true,...unsolicitedAnalysis})},{'Merge Translations':bilingualMerged})[0].json;
assert.equal(noTranslationAudit.decision.analysis,undefined);
assert.equal(JSON.stringify(noTranslationAudit.decision.parameters),bilingualBefore);
for(const [index,translation] of bilingualAudited.decision.analysis.translations.entries()){
 assert.equal(translation.conditions_zh,'25 °C 时；仅适用于 LG Chem RESU 7H_R / 10H_R');
 assert.deepEqual(translation.footnotes_zh,['使用 LG 电池时，最大电压为 495 V。','EN/IEC 62109-1, EN/IEC 62109-2']);
 assert.equal(translation.value_zh,index%3===0?'支持':index%3===1?'0.8 超前 ... 0.8 滞后':undefined);
}
const malformedTranslations=structuredClone(bilingualMerged.proposed_translations);
malformedTranslations.translations[0].footnotes_zh=['Only one note'];
malformedTranslations.translations[0].conditions_zh={unsupported:'shape'};
malformedTranslations.translations.push({parameter_id:'not-a-source-row',name_zh:'虚构参数',value_zh:'100'});
const reducedTranslation=run('Read Translation Audit',{text:JSON.stringify({approved:true,analysis:malformedTranslations})},{'Merge Translations':bilingualMerged})[0].json;
assert.equal(reducedTranslation.decision.analysis.translations.length,350);
assert.equal(reducedTranslation.decision.analysis.translations[0].name_zh,'参数');
assert.equal(reducedTranslation.decision.analysis.translations[0].value_zh,'支持');
assert.equal(reducedTranslation.decision.analysis.translations[0].footnotes_zh,undefined,'A mismatched footnote array must not shift associations');
assert.equal(reducedTranslation.decision.analysis.translations[0].conditions_zh,undefined);
assert.deepEqual(reducedTranslation.decision.analysis.translations[1],bilingualAudited.decision.analysis.translations[1]);
assert.equal(JSON.stringify(reducedTranslation.decision.parameters),bilingualBefore);
for(const unavailable of ['', '   ', 17, null, {invalid:'note'}]){
 const partialNotes=structuredClone(bilingualMerged.proposed_translations);
 partialNotes.translations[0].footnotes_zh=[unavailable,'EN/IEC 62109-1, EN/IEC 62109-2'];
 const partialMerge=run('Merge Translations',[{text:JSON.stringify({analysis:partialNotes})}],{'Build Decision':bilingual})[0].json;
 const partialAudit=run('Read Translation Audit',{text:JSON.stringify({approved:true,analysis:partialMerge.proposed_translations})},{'Merge Translations':partialMerge})[0].json;
 assert.deepEqual(partialAudit.decision.analysis.translations[0].footnotes_zh,['','EN/IEC 62109-1, EN/IEC 62109-2'],'An unavailable note keeps its position and cannot remove another supported note');
 assert.equal(partialAudit.decision.analysis.translations[0].value_zh,'支持');
 assert.equal(JSON.stringify(partialAudit.decision.parameters),bilingualBefore);
}
const stringSource=structuredClone(bilingual);
stringSource.decision.parameters[0].footnotes='Only one original footnote';
const stringNotes={translations:[{parameter_id:'p001',name_zh:'参数',footnotes_zh:['单条脚注译文']}],sections:[]};
const stringMerged=run('Merge Translations',[{text:JSON.stringify({analysis:stringNotes})}],{'Build Decision':stringSource})[0].json;
const stringAudited=run('Read Translation Audit',{text:JSON.stringify({approved:true,analysis:stringMerged.proposed_translations})},{'Merge Translations':stringMerged})[0].json;
assert.deepEqual(stringAudited.decision.analysis.translations[0].footnotes_zh,['单条脚注译文']);
assert.equal(stringAudited.decision.parameters[0].footnotes,'Only one original footnote','A string-shaped source note remains immutable');
for(const error of ['Request failed',{message:'Request failed'},{}]){
 for(const response of [
  {error,text:JSON.stringify({approved:true,analysis:bilingualMerged.proposed_translations})},
  {text:JSON.stringify({error,approved:true,analysis:bilingualMerged.proposed_translations})},
 ]){
  const failedAudit=run('Read Translation Audit',response,{'Merge Translations':bilingualMerged})[0].json;
  assert.equal(failedAudit.decision.analysis,undefined,'An explicit error cannot authorize an approved-looking response');
  assert.match(failedAudit.decision.omissions.at(-1),/review was unavailable/);
  assert.equal(JSON.stringify(failedAudit.decision.parameters),bilingualBefore);
 }
}
const enriched=run('Merge Translations',[{error:'terminated'},...batches.slice(1).map(()=>({text:'{}'}))],{'Build Decision':many})[0].json;
const reviewed=run('Read Translation Audit',{error:'terminated'},{'Merge Translations':enriched})[0].json;
assert.deepEqual(reviewed.decision.parameters,many.decision.parameters);
assert.equal(reviewed.decision.analysis,undefined);
const passed=run('Read Validation',{ok:true,valid:true},{'Read Translation Audit':reviewed})[0].json;
const preview=run('Prepare Save',{...passed,config:{...config,save_results:false}})[0].json;
assert.equal(preview.save_enabled,false);
const formatted=run('Format Result',{ok:true,completed:false,parameters:[]},{'Prepare Result':preview})[0].json;
assert.equal(formatted.output.outcome,'reviewed_preview');
assert.equal(formatted.output.parameter_count,350);
assert.equal(formatted.output.saved,false);
const final=run('Research Result',{ok:true,released:true},{'Format Result':formatted})[0].json;
assert.equal(final.outcome,'reviewed_preview');
assert.equal(final.session_released,true);
assert.equal(final.parameter_count,350);
const failed=run('Format Result',{completed:false,parameters:[]},{'Prepare Result':disconnected})[0].json;
assert.equal(failed.output.outcome,'incomplete');
assert.equal(failed.output.error.message,'terminated');
const older={ok:true,completed:true,published:true,reresearch_pending:true,product_id:'SKU',parameters:[{parameter_id:'old',value:'outdated'}],markdown:'# Prior published page',wiki_path:'products/prior',completed_at:'2025-01-01'};
const failedReresearch=run('Format Result',older,{'Prepare Result':disconnected})[0].json;
assert.equal(failedReresearch.output.outcome,'incomplete');
assert.equal(failedReresearch.output.saved,false);
assert.equal(failedReresearch.output.published,false);
assert.equal(failedReresearch.output.parameter_count,0);
assert.equal(failedReresearch.output.markdown,null);
assert.equal(failedReresearch.output.wiki_path,null);
assert.deepEqual(failedReresearch.output.previous_result,older,'Old completion remains visible as a previous result only');
assert.equal(failedReresearch.output.error.message,'terminated');
const priorOnly=run('Format Result',{...older,reresearch_pending:false},{'Prepare Result':disconnected})[0].json;
assert.equal(priorOnly.output.outcome,'incomplete','An old completion alone never proves this run saved');
assert.equal(priorOnly.output.saved,false);
const refreshPreview=run('Format Result',older,{'Prepare Result':preview})[0].json;
assert.equal(refreshPreview.output.outcome,'incomplete');
assert.equal(refreshPreview.output.parameter_count,350,'New candidate parameters do not get replaced by old stored rows');
assert.equal(refreshPreview.output.previous_result.parameters[0].value,'outdated');
const storedNew={...older,reresearch_pending:false,published:false,parameters:[{parameter_id:'p001',value:'newly saved'}],markdown:'# New page'};
const savedNew=run('Format Result',storedNew,{'Prepare Result':{...passed,saved:true}})[0].json;
assert.equal(savedNew.output.outcome,'publishable');
assert.equal(savedNew.output.saved,true);
assert.equal(savedNew.output.previous_result,null);
assert.deepEqual(savedNew.output.parameters,storedNew.parameters);
const publishClaim={work:{kind:'publication',completion:{product_id:'SKU',decision:{model:'M-500',parameters:storedNew.parameters}}},publication_configured:true};
const pending=run('Prepare Result',{ok:false,error:'Wiki is temporarily unavailable'},{'Claim One Item':publishClaim})[0].json;
const retryPublication=run('Format Result',storedNew,{'Prepare Result':pending})[0].json;
assert.equal(retryPublication.output.outcome,'publishable');
assert.equal(retryPublication.output.saved,true,'A pending-publication claim is already saved research');
assert.equal(retryPublication.output.published,false);
const successfulPublication=run('Format Result',{...storedNew,published:true},{'Prepare Result':pending})[0].json;
assert.equal(successfulPublication.output.outcome,'published');
assert.equal(successfulPublication.output.published,true);
console.log('Closed-loop workflow checks passed: source packets, English-source preference and recorded fallback, full bilingual value/condition/footnote preservation for 350 rows, translation-only output with unsolicited analysis discarded, per-note degradation without positional drift, explicit translation-error rejection, JSON error recovery vs runtime failure, independent correction/recheck, resumable completed pages, translation degradation and truthful re-research/publication results.');
require('./page-context-smoke-test.cjs');
