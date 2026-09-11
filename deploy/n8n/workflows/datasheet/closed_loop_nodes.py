"""Runtime code for the n8n-owned research and review loop (no model calls here)."""

COMMON = r"""
function parseJSON(value){
 if(value && typeof value==='object')return value;
 const text=String(value||'').trim().replace(/^\x60{3}(?:json)?\s*/i,'').replace(/\s*\x60{3}$/,'');
 try{return JSON.parse(text);}catch(error){const start=text.indexOf('{'),end=text.lastIndexOf('}');if(start>=0&&end>start)return JSON.parse(text.slice(start,end+1));throw error;}
}
function clone(value){return JSON.parse(JSON.stringify(value));}
function modelKey(value){return String(value||'').normalize('NFKC').toLowerCase().replace(/[^a-z0-9]/g,'');}
function discoveryModel(value,selected,manufacturer){
 if(typeof value!=='string')return '';
 let model=value.trim();
 const prefixes=[selected?.manufacturer_hint,manufacturer,selected?.product?.brand_code]
  .filter(prefix=>typeof prefix==='string'&&prefix.trim()).map(prefix=>prefix.trim()).sort((a,b)=>b.length-a.length);
 for(const prefix of prefixes){
  if(model.slice(0,prefix.length).toLowerCase()!==prefix.toLowerCase())continue;
  const remainder=model.slice(prefix.length);
  if(!/^[\s:\u2010-\u2015-]/.test(remainder))continue;
  const candidate=remainder.replace(/^[\s:\u2010-\u2015-]+/,'').trim();
  if(candidate){model=candidate;break;}
 }
 return model.replace(/\s*-\s*/g,'-');
}
const SOURCE_PACKET='current-page-only-v2';
function headerReference(state,headerPage,page){
 const reference={page:headerPage,role:'model_column_reference_only',model_headers:[],source_model_header_sequences:[],confirmed_model_headers:[],verified_from_prior_review:false};
 if(!Number.isInteger(headerPage)||headerPage<1||headerPage>=page)return reference;
 const target=String(state.discovery?.target_model||state.current?.model||'');
 const prior=(state.reviewed_pages||[]).find(item=>item.page===headerPage);
 const known=[target,...(prior?.rows||[]).flatMap(row=>[row.model,...(Array.isArray(row.applies_to_models)?row.applies_to_models:[])])].filter(value=>typeof value==='string'&&/[a-z]/i.test(value));
 const knownKeys=new Set(known.map(modelKey));
 const familyMatch=target.match(/^(.*?)([0-9]+(?:\.[0-9]+)?)([-_]?[a-z][a-z0-9_-]*)?$/i);
 const family=familyMatch&&/[a-z]/i.test(familyMatch[1])?familyMatch:null;
 const escape=value=>value.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
 const familyPattern=family?new RegExp('^'+escape(family[1])+'[0-9]+(?:\\.[0-9]+)?'+escape(family[3]||'')+'$','i'):null;
 const variantPattern=family?.[3]?new RegExp('^[0-9]+(?:\\.[0-9]+)?'+escape(family[3])+'$','i'):null;
 const prefix=family?modelKey(family[1]):'';
 function labels(raw){
  const accepted=[];
  for(const token of String(raw||'').match(/[a-z0-9][a-z0-9._\/-]*/gi)||[]){
   const compressed=token.match(/^(.*?[^0-9.])([0-9]+(?:\.[0-9]+)?(?:\/[0-9]+(?:\.[0-9]+)?)+)([-_]?[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*)$/i);
   const isFamily=compressed&&compressed[2].split('/').some(number=>modelKey(compressed[1]+number+compressed[3])===modelKey(target));
   if(isFamily||knownKeys.has(modelKey(token))||familyPattern?.test(token)||variantPattern?.test(token)||(prefix&&modelKey(token)===prefix))accepted.push(token);
  }
  return [...new Set(accepted)].join(' | ');
 }
 const confirmed=(prior?.history||[]).filter(entry=>entry.action==='confirmed'&&entry.evidence?.page===headerPage).map(entry=>labels(entry.evidence.column_header)).filter(Boolean);
 const source=(state.pdf?.page_texts||[]).find(item=>item.page===headerPage);
 const candidates=String(source?.plain_text||'').split(/\r?\n/).map(line=>{
  const sequence=labels(line);if(!sequence)return '';
  const allowed=new Set(sequence.split(' | ').map(token=>token.toLowerCase()));
  const tokens=line.match(/[a-z0-9][a-z0-9._\/-]*/gi)||[];
  if(/\p{L}/u.test(line.replace(/[a-z0-9][a-z0-9._\/-]*/gi,'').replace(/型号|型號|规格|規格/g,'')))return '';
  return tokens.every(token=>allowed.has(token.toLowerCase())||/^(?:model|models|type|types|typ|modell|modelle|modele|modeles|tipo|tipos|header|technical|specification|specifications)$/i.test(token))?sequence:'';
 }).filter(Boolean);
 reference.source_model_header_sequences=[...new Set(candidates)];
 reference.confirmed_model_headers=[...new Set(confirmed)];
 reference.model_headers=[...new Set([...candidates,...confirmed])];
 reference.verified_from_prior_review=confirmed.length>0;
 return reference;
}
function repairPageContext(state){
 const affected=new Set(),plan=Array.isArray(state.page_plan)?state.page_plan:[];
 const unsafe=page=>Number.isInteger(page?.header_page)&&(page.header_page>page.page||(page.header_page!==page.page&&page.source_packet_version!==SOURCE_PACKET));
 for(const page of plan){if(Number.isInteger(page.header_page)&&page.header_page>page.page){affected.add(page.page);page.header_page=page.page;}}
 for(const page of state.reviewed_pages||[])if(unsafe(page))affected.add(page.page);
 if(unsafe(state.current))affected.add(state.current.page);
 if(affected.size){
  state.reviewed_pages=(state.reviewed_pages||[]).filter(page=>!affected.has(page.page));
  const indexes=plan.map((page,index)=>affected.has(page.page)?index:-1).filter(index=>index>=0);
  if(indexes.length)state.page_cursor=Math.min(state.page_cursor||0,...indexes);
  if(state.current&&(affected.has(state.current.page)||state.current.page!==plan[state.page_cursor]?.page))state.current=null;
  for(const field of ['decision','evidence_documents','proposed_analysis','analysis_batches','proposed_translations','translation_batches','validation','validated','validation_attempts','retry_without_analysis','retry_without_translations','validation_repair_allowed','save_enabled','saved','page_prompt','audit_prompt','plan_prompt'])delete state[field];
  state.checkpoint_repaired_pages=[...affected].sort((a,b)=>a-b);
 }
 state.source_packet_version=SOURCE_PACKET;
 return state;
}
function errText(response){return typeof response?.error==='string'?response.error:response?.error?.message||'';}
function formatError(message){return /Model output doesn't fit required format|Unexpected (?:token|end).*JSON|Expected (?:double-quoted property name|property name|',' or '}')|Unterminated string in JSON|JSON.*(?:parse|syntax)/i.test(message);}
function getOutput(response){return parseJSON(response.text??response.output??response);}
function result(json,binary){return [{json,...(binary?{binary}:{})}];}
function cleanTranslations(value,parameters){
 const rows=new Map(parameters.map(row=>[row.parameter_id,row])),seen=new Set(),result=[];
 for(const item of Array.isArray(value)?value:[]){
  if(!item||typeof item!=='object'||!rows.has(item.parameter_id)||seen.has(item.parameter_id))continue;
  const row=rows.get(item.parameter_id),translation={parameter_id:item.parameter_id};
  for(const field of ['name_zh','section_zh','subsection_zh','value_zh','conditions_zh']){
   if(typeof item[field]==='string'&&item[field].trim())translation[field]=item[field];
  }
  const sourceNotes=Array.isArray(row.footnotes)?row.footnotes:typeof row.footnotes==='string'&&row.footnotes.trim()?[row.footnotes]:[];
  if(Array.isArray(item.footnotes_zh)&&item.footnotes_zh.length===sourceNotes.length)translation.footnotes_zh=item.footnotes_zh.map(note=>typeof note==='string'&&note.trim()?note:'');
  if(Object.keys(translation).length>1){seen.add(item.parameter_id);result.push(translation);}
 }
 return result;
}
function cleanRow(raw,page,model,id){
 if(!raw||typeof raw!=='object'||!String(raw.source_label||raw.name||'').trim()||raw.value===undefined||raw.value===null)return null;
 if(raw.page!==undefined&&Number(raw.page)!==page)return null;
 if(raw.model&&modelKey(raw.model)!==modelKey(model))return null;
 const row={row_id:id,source_label:String(raw.source_label||raw.name).trim(),value:raw.value,unit:String(raw.unit||''),section:String(raw.section||''),conditions:raw.conditions||'',footnotes:raw.footnotes||[],page,model,scope:raw.scope==='shared'?'shared':'model',source_kind:'parser'};
 if(row.scope==='shared'){
  if(!Array.isArray(raw.applies_to_models)||!raw.applies_to_models.some(m=>modelKey(m)===modelKey(model)))return null;
  row.applies_to_models=raw.applies_to_models.map(String);
 }
 return row;
}
const CONTRACT='n8n-datasheet-loop-v1';
"""

NODES = {
'Initialize Research': r"""
const claim=$('Claim One Item').first().json,config=$('Configuration').first().json;
const selected=claim.work.product,checkpoint=claim.work.checkpoint;
const restored=checkpoint?.context;
const compatible=restored?.workflow_contract===CONTRACT;
const restoredModel=compatible&&restored.discovery?discoveryModel(restored.discovery.target_model,selected,restored.discovery.manufacturer):'';
const modelChanged=!!restoredModel&&restoredModel!==restored.discovery.target_model;
const pdfModelMismatch=compatible&&!!restored.pdf&&(!restoredModel||[restored.pdf.target_model,restored.pdf.receipt?.target_model].some(target=>typeof target!=='string'||modelKey(target)!==modelKey(restoredModel)));
const discardCheckpoint=modelChanged||pdfModelMismatch;
const state=compatible&&!discardCheckpoint?clone(restored):{workflow_contract:CONTRACT,selected,discovery:null,pdf:null,page_plan:[],page_cursor:0,reviewed_pages:[],current:null,omissions:[]};
if(discardCheckpoint)state.checkpoint_discarded_reason='The checkpoint model or PDF receipt does not match the normalized target; rediscover and reread the PDF for correctly bound receipts.';
repairPageContext(state);
state.selected=selected;state.publication_configured=$input.first().json.publication_configured===true||claim.publication_configured===true;
const productHints=config.datasheet_hints?.[selected.product_id];
const knownDatasheetUrls=Array.isArray(productHints)?[...new Set(productHints.filter(url=>typeof url==='string'&&/^https?:\/\/\S+$/i.test(url.trim())).map(url=>url.trim()))]:[];
state.config={vision_enabled:config.vision_enabled!==false,max_review_rounds:config.max_review_rounds||3,save_results:config.save_results!==false,known_datasheet_urls:knownDatasheetUrls};
state.resumed=compatible&&!discardCheckpoint;state.error=null;state.next_action=state.pdf?'plan':'discover';
return result(state);
""",
'Discovery Input': r"""
const state=$input.first().json;
return result({...state,discovery_prompt:{product:state.selected.product,manufacturer_hint:state.selected.manufacturer_hint,domains_hint:state.selected.domains_hint,known_datasheet_urls:state.config?.known_datasheet_urls||[],preferred_datasheet_language:'en',language_policy:'Search for a matching official English datasheet first; exact model, market and version take priority. Record why a verified non-English source is needed.'}});
""",
'Read Discovery': r"""
const state=clone($('Discovery Input').last().json),response=$input.first().json;
try{const d=getOutput(response);if(errText(response)||d.found!==true||!d.manufacturer||!d.target_model||!/^https:\/\//i.test(d.url||''))throw new Error(errText(response)||d.reason||'No confirmed official datasheet');const targetModel=discoveryModel(d.target_model,state.selected,d.manufacturer);if(!targetModel)throw new Error('No confirmed manufacturer model');state.discovery={...d,target_model:targetModel,datasheet_language:typeof d.datasheet_language==='string'&&d.datasheet_language.trim()?d.datasheet_language.trim():'unknown',language_selection_reason:typeof d.language_selection_reason==='string'?d.language_selection_reason:''};if(targetModel!==d.target_model)state.discovery.reported_target_model=d.target_model;state.found=true;}
catch(error){state.found=false;state.error={stage:'discovery',message:error.message};}
return result(state);
""",
'Read PDF Result': r"""
const state=clone($('Read Discovery').last().json),pdf=$input.first().json;
state.pdf_available=pdf.ok===true&&!!pdf.document_id&&!!pdf.receipt;
if(state.pdf_available){state.pdf=pdf;state.page_plan=[];state.page_cursor=0;state.reviewed_pages=[];state.current=null;state.omissions=[];state.next_action='plan';}
else state.error={stage:'pdf',message:pdf.error||'Original PDF was not available'};
return result(state);
""",
'Prepare Page Plan': r"""
const state=$input.first().json;
const pages=Array.isArray(state.pdf.page_texts)?state.pdf.page_texts:[];
state.plan_prompt={target_model:state.discovery.target_model,manufacturer:state.discovery.manufacturer,pages:pages.map(p=>({page:p.page,plain_text:p.plain_text,has_text:!!p.plain_text,truncated:p.truncated}))};
state.plan_ready=Array.isArray(state.page_plan)&&state.page_plan.length===state.pdf.page_count;
return result(state);
""",
'Read Page Plan': r"""
const state=clone($('Prepare Page Plan').last().json),response=$input.first().json;
let proposed=[];
try{const parsed=getOutput(response);if(Array.isArray(parsed.pages))proposed=parsed.pages;}catch(error){state.omissions.push('Page planning could not be parsed; inspect every page.');}
state.page_plan=Array.from({length:state.pdf.page_count},(_,i)=>{
 const page=i+1,text=state.pdf.page_texts?.find(p=>p.page===page),suggestion=proposed.find(p=>p.page===page);
 const exact=text?.plain_text&&modelKey(text.plain_text).includes(modelKey(state.discovery.target_model));
 const applicable=!text?.plain_text||exact||!suggestion||suggestion.applicability!=='unrelated';
 const header=Number.isInteger(suggestion?.header_page)&&suggestion.header_page>=1&&suggestion.header_page<=page?suggestion.header_page:page;
 return {page,applicability:applicable?'review':'unrelated',header_page:header,reason:String(suggestion?.reason||'Unresolved page applicability; inspect source')};
});
delete state.plan_prompt;state.next_action='choose_page';return result(state);
""",
'Choose Page': r"""
const state=repairPageContext(clone($input.first().json));
delete state.plan_prompt;
// A checkpoint can be committed after the last audit but before Finish Page.
// Promote its completed page once, so resuming does not spend another model call.
if(state.current?.done===true){
 const c=state.current,index=state.page_plan.findIndex(p=>p.page===c.page);
 if(index>=0){
  state.reviewed_pages=state.reviewed_pages.filter(p=>p.page!==c.page);
  state.reviewed_pages.push({page:c.page,header_page:c.header_page,source_packet_version:c.source_packet_version,applicability:c.applicability||'target',rows:c.rows,omissions:c.omissions||[],history:c.history||[],coverage:c.coverage||{},receipts:c.render_receipts||[],has_images:c.has_images,rounds:c.round});
  if(state.page_cursor===index)state.page_cursor++;
  state.current=null;
 }
}
while(state.page_cursor<state.page_plan.length&&(state.page_plan[state.page_cursor].applicability==='unrelated'||state.reviewed_pages.some(p=>p.page===state.page_plan[state.page_cursor].page))){
 const plan=state.page_plan[state.page_cursor++];
 if(!state.reviewed_pages.some(p=>p.page===plan.page))state.reviewed_pages.push({page:plan.page,applicability:'unrelated',rows:[],omissions:[],history:[],reason:plan.reason,receipts:[]});
}
state.has_page=state.page_cursor<state.page_plan.length;
if(!state.has_page){state.current=null;return result(state);}
const plan=state.page_plan[state.page_cursor],source=state.pdf.page_texts?.find(p=>p.page===plan.page)||{};
if(!Number.isInteger(plan.header_page)||plan.header_page<1||plan.header_page>plan.page)plan.header_page=plan.page;
if(!state.current||state.current.page!==plan.page||state.current.header_page!==plan.header_page){
 state.current={page:plan.page,header_page:plan.header_page,source_packet_version:SOURCE_PACKET,model:state.discovery.target_model,plain_text:source.plain_text||'',layout_text:source.layout_text||'',text_truncated:source.truncated===true,rows:[],round:0,revision:0,omissions:[],history:[],render_receipts:[],has_images:false,extracted:false};
}
state.current.source_packet_version=SOURCE_PACKET;
state.current.header_reference=headerReference(state,plan.header_page,plan.page);
state.current.header_text=state.current.header_reference.model_headers.join('\n');
state.extraction_needed=state.current.extracted!==true;
state.page_prompt={page:plan.page,header_page:plan.header_page,target_model:state.discovery.target_model,manufacturer:state.discovery.manufacturer,plain_text:state.current.plain_text,layout_text:state.current.layout_text,header_text:state.current.header_text,header_reference:state.current.header_reference,text_truncated:state.current.text_truncated};
return result(state);
""",
'Attach Extraction Images': r"""
const state=clone($('Choose Page').last().json),response=$input.first().json,binary={};
for(const [key,page] of [['page',state.current.page]]){
 const image=(response.images||[]).find(image=>image.page===page);
 if(image?.data)binary[key]={data:image.data,mimeType:image.mime_type||'image/png',fileName:'datasheet-'+page+'.png',fileExtension:'png'};
}
state.current.has_images=!!binary.page;
if(response.receipt)state.current.render_receipts.push(response.receipt);
state.extraction_has_images=state.current.has_images;
return result(state,binary);
""",
'Read Page Draft': r"""
let state;
try{state=clone($('Attach Extraction Images').last().json);}catch{state=clone($('Choose Page').last().json);}
if(state.current.page!==$('Choose Page').last().json.current.page)state=clone($('Choose Page').last().json);
const response=$input.first().json;
try{
 const parsed=getOutput(response);if(errText(response))throw new Error(errText(response));
 const source=Array.isArray(parsed.parameters)?parsed.parameters:parsed.rows;
 if(!Array.isArray(source))throw new Error('Page extraction did not return a parameter array');
 if(source.some(row=>row?.page!==undefined&&Number(row.page)!==state.current.page))state.current.omissions.push('Rows explicitly attributed to another page were ignored; only this current page can supply parameter values.');
 state.current.rows=source.map((row,i)=>cleanRow(row,state.current.page,state.current.model,'page'+state.current.page+'-row'+(i+1))).filter(Boolean);
 state.current.extracted=true;state.current.omissions.push(...(Array.isArray(parsed.omissions)?parsed.omissions.map(String):[]));
 state.current.applicability=parsed.applicability||'uncertain';
 state.next_action='audit';
}catch(error){state.next_action='stop';state.error={stage:'extraction',page:state.current.page,message:error.message};}
delete state.page_prompt;return result(state);
""",
'Prepare Audit': r"""
const state=clone($input.first().json),c=state.current;c.has_images=false;
state.audit_prompt={page:c.page,revision:c.revision,header_page:c.header_page,target_model:c.model,plain_text:c.plain_text,layout_text:c.layout_text,header_text:c.header_text,header_reference:c.header_reference,text_truncated:c.text_truncated,parameters:c.rows,previous_feedback:c.feedback||'',previous_omissions:c.omissions||[]};
return result(state);
""",
'Attach Audit Images': r"""
const state=clone($('Prepare Audit').last().json),response=$input.first().json,binary={};
for(const [key,page] of [['page',state.current.page]]){
 const image=(response.images||[]).find(image=>image.page===page);
 if(image?.data)binary[key]={data:image.data,mimeType:image.mime_type||'image/png',fileName:'audit-'+page+'.png',fileExtension:'png'};
}
state.current.has_images=!!binary.page;
if(response.receipt)state.current.render_receipts.push(response.receipt);
state.audit_has_images=state.current.has_images;
state.needs_crop=state.current.has_images&&Array.isArray(state.current.region)&&state.current.region.length===4;
return result(state,binary);
""",
'Attach Audit Crop': r"""
const previous=$('Attach Audit Images').last(),state=clone(previous.json),response=$input.first().json,binary={...(previous.binary||{})};
const image=(response.images||[]).find(image=>image.page===state.current.page);
if(image?.data){binary.page={data:image.data,mimeType:image.mime_type||'image/png',fileName:'audit-crop.png',fileExtension:'png'};state.audit_prompt.crop_region=state.current.region;if(response.receipt)state.current.render_receipts.push(response.receipt);}
return result(state,binary);
""",
'Apply Page Audit': r"""
let state=clone($('Prepare Audit').last().json);
try{const attached=$('Attach Audit Images').last().json;if(attached.current.page===state.current.page&&attached.current.revision===state.current.revision)state=clone(attached);}catch{}
try{const cropped=$('Attach Audit Crop').last().json;if(cropped.current.page===state.current.page&&cropped.current.revision===state.current.revision)state=clone(cropped);}catch{}
const response=$input.first().json,message=errText(response);
if(message&&!formatError(message)){
 state.error={stage:'audit',page:state.current.page,message};state.next_action='stop';
}else{
 let review;try{review=getOutput(response);}catch(error){review={error:error.message};}
 state.current=applyPageAudit(state.current,review,{max_rounds:state.config.max_review_rounds});
 if(message)state.current.feedback='The previous response was invalid or incomplete JSON. Return a complete compact JSON object; avoid lengthy explanations. '+message;
 state.next_action=state.current.done?'page_done':'audit';
}
delete state.audit_prompt;return result(state);
""",
'Finish Page': r"""
const state=clone($input.first().json),c=state.current;
state.reviewed_pages=state.reviewed_pages.filter(p=>p.page!==c.page);
state.reviewed_pages.push({page:c.page,header_page:c.header_page,source_packet_version:c.source_packet_version,applicability:c.applicability||'target',rows:c.rows,omissions:c.omissions||[],history:c.history||[],coverage:c.coverage||{},receipts:c.render_receipts||[],has_images:c.has_images,rounds:c.round});
state.page_cursor++;state.current=null;state.next_action='choose_page';return result(state);
""",
'Progress Snapshot': r"""
const state=clone($input.first().json);
delete state.page_prompt;delete state.audit_prompt;delete state.plan_prompt;
state.checkpoint_stage=state.current?'draft':state.reviewed_pages?.some(p=>p.rows?.length)?'reviewed':'pdf_ready';
return result(state);
""",
'Restore Progress': r"""
const state=clone($('Progress Snapshot').last().json),response=$input.first().json;
if(response.ok!==true){state.error={stage:'checkpoint',message:response.error||'Could not persist research progress'};state.next_action='stop';}
return result(state);
""",
'Build Decision': r"""
const state=clone($input.first().json),parameters=[],seen=new Set(),receipts=[],omissions=[...(state.omissions||[])];
for(const page of [...state.reviewed_pages].sort((a,b)=>a.page-b.page)){
 omissions.push(...(page.omissions||[]).map(x=>'Page '+page.page+': '+(typeof x==='string'?x:JSON.stringify(x))));
 for(const receipt of page.receipts||[]){const key=JSON.stringify(receipt);if(!receipts.some(r=>JSON.stringify(r)===key))receipts.push(receipt);}
 for(const raw of page.rows||[]){
  const row=clone(raw);delete row.row_id;delete row.audit_status;delete row.evidence;delete row.review_evidence;delete row._review;
  const key=JSON.stringify([row.page,row.section,row.source_label,row.value,row.unit,row.conditions,row.footnotes,row.scope]);
  if(seen.has(key))continue;seen.add(key);
  row.parameter_id='p'+String(parameters.length+1).padStart(3,'0');row.status='confirmed';row.model=state.discovery.target_model;
  parameters.push(row);
 }
}
state.decision={schema_version:'4',product_id:state.selected.product_id,expected_revision:state.selected.revision,outcome:'publish',manufacturer:state.discovery.manufacturer,model:state.discovery.target_model,official_source:{url:state.pdf.url,document_sha256:state.pdf.artifact_sha256,official_page_url:state.pdf.official_page_url||state.discovery.official_page_url||'',verification_reason:state.discovery.verification_reason||'Official manufacturer datasheet'},parameters,visual_review:{status:receipts.length?'reviewed':state.config.vision_enabled?'failed':'unavailable',receipts},omissions,summary:'Manufacturer datasheet parameters for '+state.discovery.target_model,product_description_zh:state.discovery.manufacturer+' '+state.discovery.target_model+'；参数来自官方数据表，已逐页核对。',parameter_enrichment:null};
state.decision.official_source.datasheet_language=state.discovery.datasheet_language||'unknown';
state.decision.official_source.language_selection_reason=state.discovery.language_selection_reason||'';
state.evidence_documents=[{url:state.pdf.url,content:state.pdf.content||'',receipt:state.pdf.receipt}];
state.has_parameters=parameters.length>0;
if(!state.has_parameters)state.error={stage:'review',message:'No parameters could be confirmed from the official source'};
return result(state);
""",
'Prepare Translation Batches': r"""
const state=$input.first().json,rows=state.decision.parameters;
return Array.from({length:Math.ceil(rows.length/25)},(_,i)=>({json:{batch:i,model:state.decision.model,manufacturer:state.decision.manufacturer,datasheet_language:state.decision.official_source?.datasheet_language||'unknown',parameters:rows.slice(i*25,(i+1)*25)}}));
""",
'Merge Translations': r"""
const state=clone($('Build Decision').last().json),translations=[];
delete state.decision.analysis;
for(const item of $input.all()){
 try{const parsed=getOutput(item.json),extra=Array.isArray(parsed.translations)?parsed:parsed.analysis||parsed;if(errText(item.json)||errText(parsed))throw new Error(errText(item.json)||errText(parsed));
  translations.push(...cleanTranslations(extra.translations,state.decision.parameters));
 }catch{state.decision.omissions.push('Parameter translation was unavailable for a parameter group.');}
}
state.proposed_translations={translations:cleanTranslations(translations,state.decision.parameters)};return result(state);
""",
'Read Translation Audit': r"""
const state=clone($('Merge Translations').last().json);
delete state.decision.analysis;
try{
 const response=$input.first().json;if(response.error)throw new Error(errText(response)||'Translation review returned an error');
 const answer=getOutput(response);if(answer.error)throw new Error(errText(answer)||'Translation review returned an error');
 const translations=cleanTranslations(Array.isArray(answer.translations)?answer.translations:answer.analysis?.translations,state.decision.parameters);
 if(answer.approved===true&&translations.length)state.decision.analysis={translations};
 else state.decision.omissions.push('Parameter translations were omitted after review; confirmed parameters retained.');
}catch{state.decision.omissions.push('Translation review was unavailable; confirmed parameters retained.');}
delete state.proposed_translations;return result(state);
""",
'Read Validation': r"""
let state;try{state=clone($('Read Translation Audit').last().json);}catch{state=clone($('Build Decision').last().json);}
const response=$input.first().json;state.validation=response;state.validated=response.ok===true&&response.valid===true;
if(!state.validated&&state.decision.analysis){delete state.decision.analysis;state.decision.omissions.push('Parameter translations did not meet storage schema; confirmed parameters retained.');state.retry_without_translations=true;}
else{state.retry_without_translations=false;if(!state.validated)state.error={stage:'validation',message:JSON.stringify(response.errors||response.error||response)};}
return result(state);
""",
'Read Parameter Validation': r"""
const state=clone($('Read Validation').last().json),response=$input.first().json;state.validation=response;state.validated=response.ok===true&&response.valid===true;state.retry_without_translations=false;
if(!state.validated)state.error={stage:'validation',message:JSON.stringify(response.errors||response.error||response)};return result(state);
""",
'Prepare Save': r"""
const state=$input.first().json;state.save_enabled=state.config.save_results!==false;return result(state);
""",
'Prepare Validation Repair': r"""
const state=clone($input.first().json),response=state.validation||{};
state.validation_attempts=(state.validation_attempts||0)+1;
const repairable=response.valid===false||response.code==='validation_failed';
const message=JSON.stringify(response.errors||response.error||response),match=message.match(/parameters\[(\d+)\]/);
const page=match?state.decision.parameters[Number(match[1])]?.page:state.reviewed_pages.find(p=>p.rows?.length)?.page;
const previous=state.reviewed_pages.find(p=>p.page===page),index=state.page_plan.findIndex(p=>p.page===page);
state.validation_repair_allowed=repairable&&state.validation_attempts<=2&&!!previous&&index>=0;
if(state.validation_repair_allowed){
 const source=state.pdf.page_texts.find(p=>p.page===page)||{};
 const headerPage=Number.isInteger(previous.header_page)&&previous.header_page>=1&&previous.header_page<=page?previous.header_page:page;
 const reference=headerReference(state,headerPage,page);
 state.current={page,header_page:headerPage,source_packet_version:SOURCE_PACKET,model:state.discovery.target_model,plain_text:source.plain_text||'',layout_text:source.layout_text||'',header_text:reference.model_headers.join('\n'),header_reference:reference,text_truncated:source.truncated===true,rows:previous.rows,round:0,revision:state.validation_attempts,omissions:previous.omissions||[],history:previous.history||[],render_receipts:previous.receipts||[],has_images:false,extracted:true,feedback:'Correct the server validation errors using the source page: '+message};
 state.reviewed_pages=state.reviewed_pages.filter(p=>p.page!==page);state.page_cursor=index;state.next_action='audit';state.error=null;
}
return result(state);
""",
'Read Save Result': r"""
const state=clone($('Prepare Save').last().json),response=$input.first().json;state.saved=response.ok===true;
if(!state.saved)state.error={stage:'save',message:response.error||JSON.stringify(response)};state.publish_enabled=state.saved&&state.publication_configured===true;return result(state);
""",
'Prepare Result': r"""
const state=$input.first().json;
if(state?.selected)return result(state);
const claim=$('Claim One Item').first().json;
return result({selected:{product_id:claim.work?.completion?.product_id},decision:claim.work?.completion?.decision,saved:!!claim.work?.completion,publication_configured:claim.publication_configured===true,publish_result:state});
""",
'Keep Published Context': r"""
const state=clone($('Read Save Result').last().json);state.publish_result=$input.first().json;return result(state);
""",
'Format Result': r"""
const state=clone($('Prepare Result').last().json),stored=$input.first().json;
// A stored completion may be the old result deliberately retained during
// re-research. Only this execution's save or its pending-publication claim counts.
const completed=state.saved===true&&stored.reresearch_pending!==true;
const published=completed&&stored.published===true;
const parameters=completed&&stored.completed===true?stored.parameters||[]:state.decision?.parameters||[];
const previous_result=!completed&&(stored.completed===true||stored.status==='completed')?clone(stored):null;
state.output={outcome:published?'published':completed?'publishable':stored.reresearch_pending===true?'incomplete':state.validated?'reviewed_preview':'incomplete',product_id:state.selected?.product_id,model:(completed?stored.model:null)||state.decision?.model||state.discovery?.target_model,parameter_count:parameters.length,saved:completed,published,wiki_path:completed?stored.wiki_path||null:null,markdown:completed?stored.markdown||null:null,parameters,previous_result,reresearch_pending:stored.reresearch_pending===true,omissions:state.decision?.omissions||[],error:state.error||null,review_summary:(state.reviewed_pages||[]).map(p=>({page:p.page,applicability:p.applicability,parameter_count:p.rows?.length||0,rounds:p.rounds||0,omissions:p.omissions||[]})),reviewed_pages:state.reviewed_pages||[],publication_configured:state.publication_configured===true};
return result(state);
""",
'Research Result': r"""
const state=$('Format Result').last().json;return result({...state.output,session_released:$input.first().json.released===true});
""",
'No Work Result': r"""
const claim=$input.first().json;return result({outcome:'no_work',saved:false,published:false,reason:claim.reason||'No claim acquired'});
""",
}
