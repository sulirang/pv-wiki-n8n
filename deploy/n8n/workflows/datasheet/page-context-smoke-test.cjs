'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const definition=JSON.parse(fs.readFileSync(path.join(__dirname,'pv-wiki-datasheet-agent.json'),'utf8'));
const nodes=Object.fromEntries(definition.nodes.map(node=>[node.name,node]));
const copy=value=>JSON.parse(JSON.stringify(value));
function run(name,input,refs={}){
 const lookup=name=>{if(!(name in refs))throw new Error('Missing '+name);return{first:()=>({json:refs[name]}),last:()=>({json:refs[name]})};};
 return new Function('$input','$',nodes[name].parameters.jsCode)({first:()=>({json:input}),all:()=>[{json:input}]},lookup)[0];
}
const selected={product_id:'page-test',source_hash:'hash',revision:'new:hash',manufacturer_hint:'Maker',product:{brand_code:'Maker',product_name:'Maker M-500'}};
const config={vision_enabled:true,max_review_rounds:3,save_results:true};
const source={ok:true,document_id:'a'.repeat(64),target_model:'M-500',receipt:{target_model:'M-500',signature:'signed-pdf'},page_count:2,page_texts:[
 {page:1,plain_text:'M-500\nReference-only rating 9,999 W\nReference-only footnote KEEP_ON_PAGE_1',layout_text:'M-500\nReference-only rating 9,999 W'},
 {page:2,plain_text:'Efficiency 98.5 %\nCURRENT_PAGE_NOTE',layout_text:'Efficiency 98.5 %'},
]};
const priorRow={row_id:'prior-row',source_label:'Reference-only rating',value:'9,999',unit:'W',page:1,model:'M-500',scope:'model',footnotes:['KEEP_ON_PAGE_1']};
function base(){
 const state=run('Initialize Research',{publication_configured:false},{'Claim One Item':{work:{product:selected}},Configuration:config}).json;
 return {...state,discovery:{manufacturer:'Maker',target_model:'M-500',url:'https://maker.example/pdf'},pdf:copy(source),page_plan:[{page:1,header_page:1,applicability:'review'},{page:2,header_page:1,applicability:'review'}],page_cursor:1,reviewed_pages:[{page:1,header_page:1,rows:[copy(priorRow)],receipts:[{pages:[1],signature:'view-prior'}],history:[{action:'confirmed',before:copy(priorRow),evidence:{page:1,column_header:'M-500',raw_label:'Reference-only rating',raw_value:'9,999 W'}}]}]};
}
const planInput=base();planInput.page_plan=[];
const planned=run('Read Page Plan',{text:JSON.stringify({pages:[{page:1,header_page:2,applicability:'target'},{page:2,header_page:2,applicability:'target'}]})},{'Prepare Page Plan':planInput}).json;
assert.equal(planned.page_plan[0].header_page,1,'A future technical table cannot become the cover header');
const chosen=run('Choose Page',base()).json;
assert.equal(chosen.current.header_page,1);
assert.equal(chosen.current.source_packet_version,'current-page-only-v2');
assert.deepEqual(chosen.page_prompt.header_reference.model_headers,['M-500']);
assert.equal(chosen.page_prompt.header_reference.verified_from_prior_review,true);
const ordered=base();ordered.pdf.page_texts[0].plain_text='Model M-100 M-500 M-900\nM-500 has a 9,999 W reference-only rating\nM-900 precedes M-100 in this sentence\nM-900 的说明里提到了 M-100';
const orderedReference=run('Choose Page',ordered).json.page_prompt.header_reference;
assert.deepEqual(orderedReference.source_model_header_sequences,['M-100 | M-500 | M-900']);
assert.deepEqual(orderedReference.confirmed_model_headers,['M-500']);
assert.deepEqual(orderedReference.model_headers,['M-100 | M-500 | M-900','M-500'],'A single confirmed target does not replace the actual full column order');
for(const forbidden of ['9,999','Reference-only rating','KEEP_ON_PAGE_1'])assert.equal(JSON.stringify(chosen.page_prompt).includes(forbidden),false,'Reference-page facts cannot enter the extraction prompt');
const pageImage=Buffer.from('CURRENT_PAGE_IMAGE').toString('base64'),headerImage=Buffer.from('OTHER_PAGE_ENTIRE_TABLE').toString('base64');
for(const [name,ref] of [['Attach Extraction Images','Choose Page'],['Attach Audit Images','Prepare Audit']]){
 const input=name==='Attach Audit Images'?run('Prepare Audit',chosen).json:chosen;
 const attached=run(name,{images:[{page:1,data:headerImage},{page:2,data:pageImage}],receipt:{signature:'current-view'}},{[ref]:input});
 assert.deepEqual(Object.keys(attached.binary),['page']);
 assert.equal(attached.binary.page.data,pageImage);
 assert.equal(attached.json.current.has_images,true);
 const onlyWrong=run(name,{images:[{page:1,data:headerImage}]},{[ref]:input});
 assert.equal(onlyWrong.json.current.has_images,false,'A reference-page image alone never proves the current page was seen');
}
const misplaced=run('Read Page Draft',{text:JSON.stringify({applicability:'target',parameters:[{source_label:'Reference-only rating',value:'9,999',unit:'W',model:'M-500',scope:'model',page:1}]})},{'Choose Page':chosen}).json;
assert.deepEqual(misplaced.current.rows,[]);
assert.ok(misplaced.current.omissions.some(note=>note.includes('another page')));
const currentDraft=run('Read Page Draft',{text:JSON.stringify({applicability:'target',parameters:[{source_label:'Efficiency',value:'98.5',unit:'%',model:'M-500',scope:'model',page:2}]})},{'Choose Page':chosen}).json;
assert.equal(currentDraft.current.rows.length,1);
const audit=run('Prepare Audit',currentDraft).json;
for(const forbidden of ['9,999','Reference-only rating','KEEP_ON_PAGE_1'])assert.equal(JSON.stringify(audit.audit_prompt).includes(forbidden),false,'Reference-page facts cannot enter the independent audit prompt');
const scanned=base();scanned.pdf.page_texts[0].plain_text='';scanned.pdf.page_texts[1].plain_text='';
const scanChoice=run('Choose Page',scanned).json;
assert.deepEqual(scanChoice.page_prompt.header_reference.model_headers,['M-500'],'An already-reviewed model header remains useful for a scanned continuation');
assert.equal(scanChoice.page_prompt.header_reference.verified_from_prior_review,true);
scanned.reviewed_pages[0].history=[];
const unknownScan=run('Choose Page',scanned).json;
assert.deepEqual(unknownScan.page_prompt.header_reference.model_headers,[],'Missing scanned header evidence is not guessed from the task model');
assert.equal(unknownScan.page_prompt.header_reference.verified_from_prior_review,false);
const numeric=base();numeric.discovery.target_model='500';numeric.reviewed_pages=[];
numeric.pdf.page_texts[0].plain_text='99.9 500 350 450';
assert.deepEqual(run('Choose Page',numeric).json.page_prompt.header_reference.model_headers,[],'Numeric facts cannot be reclassified as header labels');
const repair=copy(currentDraft);
repair.validation={valid:false,errors:['parameters[0] needs source review']};
repair.decision={parameters:[{...repair.current.rows[0],parameter_id:'p001'}]};
repair.reviewed_pages.push({page:2,header_page:1,source_packet_version:'current-page-only-v2',rows:copy(repair.current.rows),receipts:[{signature:'current-view'}]});
const repaired=run('Prepare Validation Repair',repair).json;
assert.equal(repaired.validation_repair_allowed,true);
assert.equal(repaired.current.header_text,'M-500');
for(const forbidden of ['9,999','Reference-only rating','KEEP_ON_PAGE_1'])assert.equal(JSON.stringify(run('Prepare Audit',repaired).json.audit_prompt).includes(forbidden),false,'Validation repair cannot reintroduce the old whole-header-page input');
function resume(checkpoint){return run('Initialize Research',{publication_configured:false},{'Claim One Item':{work:{product:selected,checkpoint:{context:checkpoint}}},Configuration:config}).json;}
const unsafe=base();
unsafe.page_plan[0].header_page=2;unsafe.page_cursor=0;
unsafe.current={page:1,header_page:2,done:true,rows:[{...priorRow,value:'98.5'}],render_receipts:[{pages:[1,2],signature:'mixed-view'}]};
unsafe.reviewed_pages=[{page:1,header_page:2,rows:[{...priorRow,value:'98.5'}],receipts:[{signature:'bad-view'}]},{page:2,header_page:2,rows:[{page:2,value:'98.5'}],receipts:[{signature:'good-view'}]}];
unsafe.decision={parameters:[{page:1,value:'98.5'}]};unsafe.evidence_documents=[{receipt:'old'}];unsafe.proposed_analysis={bad:'derived'};unsafe.validation_attempts=2;
const before=JSON.stringify(unsafe),reset=resume(unsafe);
assert.deepEqual(reset.pdf,source,'Correctly model-bound PDF content and receipt stay cached');
assert.deepEqual(reset.selected,selected);assert.equal(reset.current,null);assert.equal(reset.page_cursor,0);
assert.equal(reset.page_plan[0].header_page,1);
assert.deepEqual(reset.reviewed_pages.map(page=>page.page),[2]);
assert.deepEqual(reset.reviewed_pages[0].receipts,[{signature:'good-view'}]);
for(const field of ['decision','evidence_documents','proposed_analysis','validation_attempts'])assert.equal(reset[field],undefined);
assert.equal(JSON.stringify(unsafe),before);
const directlyChosen=run('Choose Page',unsafe).json;
assert.equal(directlyChosen.current.page,1);assert.equal(directlyChosen.current.header_page,1);assert.equal(directlyChosen.current.done,undefined);
assert.deepEqual(directlyChosen.current.rows,[],'Invalid completed drafts cannot be promoted before context repair');
const backward=base();
backward.current={...copy(chosen.current),source_packet_version:undefined,done:true,rows:[{page:2,value:'9,999'}],render_receipts:[{pages:[1,2]}]};
backward.reviewed_pages.push({page:2,header_page:1,rows:[{page:2,value:'9,999'}],receipts:[{pages:[1,2]}]});
const backwardReset=resume(backward);
assert.deepEqual(backwardReset.reviewed_pages.map(page=>page.page),[1]);
assert.equal(backwardReset.current,null);assert.equal(backwardReset.page_cursor,1);assert.equal(backwardReset.page_plan[1].header_page,1);
assert.deepEqual(backwardReset.pdf,source);
const safe=base();safe.current=copy(chosen.current);
const safeResume=resume(safe);
assert.deepEqual(safeResume.current,safe.current,'A current-page-only continuation packet can resume normally');
assert.deepEqual(safeResume.pdf,safe.pdf);
console.log('Page-context isolation passed: forward-header rejection, current-page-only images/text facts, continued/scanned model references, explicit row-page mismatch, repair-path isolation and selective checkpoint recovery.');
