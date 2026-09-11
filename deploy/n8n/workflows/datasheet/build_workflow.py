"""Generate inactive n8n workflow JSON and equivalent MCP SDK source."""
import json
from pathlib import Path
from uuid import uuid5,NAMESPACE_URL
HERE=Path(__file__).resolve().parent
nodes=[]; connections={}
def add(name,typ,ver,parameters=None,**kw):
    nodes.append(dict(name=name,type=typ,typeVersion=ver,parameters=parameters or {},id=str(uuid5(NAMESPACE_URL,'pvwiki/'+name)),position=[len(nodes)%7*260,len(nodes)//7*240],**kw));return name
def link(a,b,i=0,kind='main'):
    outs=connections.setdefault(a,{}).setdefault(kind,[])
    while len(outs)<=i:outs.append([])
    outs[i].append(dict(node=b,type=kind,index=0))
def code(name,body):
    return add(name,'n8n-nodes-base.code',2,dict(mode='runOnceForAllItems',jsCode=body.strip()),onError='continueErrorOutput')
def gate(name,exp):
    return add(name,'n8n-nodes-base.if',2.2,dict(conditions=dict(options=dict(caseSensitive=True,leftValue='',typeValidation='strict',version=2),conditions=[dict(leftValue='={{ '+exp+' }}',operator=dict(type='boolean',operation='true',singleValue=True))],combinator='and'),options={}))
def http(name,path,body=None,tool=False,lease=True,method='POST',soft=False):
    p=dict(method=method,url="={{ $('Configuration').first().json.worker_url + "+json.dumps(path)+' }}',authentication='genericCredentialType',genericAuthType='httpHeaderAuth',options=dict(timeout=300000,response=dict(response=dict(responseFormat='json'))))
    if body is not None:p.update(sendBody=True,specifyBody='json',jsonBody=body)
    if lease:p.update(sendHeaders=True,headerParameters=dict(parameters=[dict(name='X-PV-Wiki-Lease',value="={{ $('Claim One Item').first().json.lease_token }}")]))
    return add(name,'n8n-nodes-base.httpRequestTool' if tool else 'n8n-nodes-base.httpRequest',4.2,p,**({} if tool else dict(onError='continueRegularOutput' if soft else 'continueErrorOutput')))
def chain(name,system,prompt='={{ JSON.stringify($json) }}',images=False):
    m=[dict(type='SystemMessagePromptTemplate',message=system)]
    if images:m += [dict(type='HumanMessagePromptTemplate',messageType='imageBinary',binaryImageDataKey=key,imageDetail='high') for key in ['anchor','previous','page']]
    return add(name,'@n8n/n8n-nodes-langchain.chainLlm',1.9,dict(promptType='define',text=prompt,messages=dict(messageValues=m),batching=dict(batchSize=1)),onError='continueRegularOutput')
def agent(name,system):
    return add(name,'@n8n/n8n-nodes-langchain.agent',3.1,dict(promptType='define',text='={{ JSON.stringify($json) }}',options=dict(systemMessage=system,maxIterations=30,returnIntermediateSteps=False,passthroughBinaryImages=False)),onError='continueRegularOutput')
from closed_loop_graph import configure
from closed_loop_nodes import COMMON
configure(add, link, code, gate, http, chain, agent, HERE, nodes)
wf=dict(name='PV Wiki - Official Datasheet Agent',nodes=nodes,connections=connections,pinData={},active=False,settings=dict(executionOrder='v1',timezone='Asia/Shanghai'),tags=[])
HERE.mkdir(parents=True,exist_ok=True)
(HERE/'pv-wiki-datasheet-agent.json').write_text(json.dumps(wf,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def js(v):
    if isinstance(v,str) and v.startswith('=') and '{{' in v:return 'expr('+json.dumps(v[1:],ensure_ascii=False)+')'
    if isinstance(v,str) and v.startswith(COMMON.strip()):return 'commonCode+'+json.dumps(v[len(COMMON.strip()):],ensure_ascii=False)
    if isinstance(v,dict):return '{'+','.join(json.dumps(k)+':'+js(x) for k,x in v.items())+'}'
    if isinstance(v,list):return '['+','.join(js(x) for x in v)+']'
    return json.dumps(v,ensure_ascii=False)
ids={n['name']:'n'+str(i) for i,n in enumerate(nodes)}
SAMPLE={"ok":True,"acquired":True,"lease_token":"sample-token","work":{"kind":"research","completion":{"product_id":"sample-id"}},"found":True,"discovery":{"url":"https://manufacturer.example/file","target_model":"MODEL","official_page_url":"https://manufacturer.example/model"},"document_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","receipt":{},"page_count":2,"restart_page_loop":True,"vision_enabled":True,"has_images":True,"page":2,"context":{"discovery":{"target_model":"MODEL","manufacturer":"Maker"},"pdf":{"parameter_rows":[],"parse_status":"text"}},"selected":{"product_id":"sample-id","source_hash":"hash"},"contract":{"schema_version":"4"},"decision":{"parameters":[]},"evidence_documents":[],"candidate_ready":True,"review_passed":True,"validated":True,"saved":True,"retry_allowed":True,"needs_search":False,"needs_view":False,"pages":[1,2],"region":None}
SAMPLE = dict(ok=True, acquired=True, work=dict(kind='research', completion=dict(product_id='example')), lease_token='example',
    selected=dict(product_id='example', source_hash='example', revision='new:example'),
    publication_configured=False, saved=False, found=True, validated=True,
    pdf_available=True, plan_ready=True, has_page=True, extraction_needed=True,
    extraction_has_images=True, audit_has_images=True, needs_crop=False,
    has_parameters=True, retry_without_translations=False, validation_repair_allowed=True,
    save_enabled=True, publish_enabled=False, next_action='audit', checkpoint_stage='draft',
    config=dict(vision_enabled=True, max_review_rounds=3, save_results=True),
    discovery_prompt={}, plan_prompt={}, page_prompt={}, audit_prompt={}, proposed_translations={},
    pdf=dict(document_id='example', url='https://manufacturer.example/download', page_texts=[]),
    discovery=dict(url='https://manufacturer.example/download', target_model='MODEL', official_page_url='https://manufacturer.example/model'),
    current=dict(page=2, header_page=2, revision=0, round=0, rows=[], plain_text='example', text_truncated=False),
    decision=dict(parameters=[], model='MODEL'))
subs=[n for n in nodes if n['type'].endswith(('lmChatOpenAi','toolThink','httpRequestTool'))]
normal=[n for n in nodes if n not in subs];sdk=[
    'const commonCode = '+json.dumps(COMMON.strip(),ensure_ascii=False)+';',
    'const sampleOutput = ['+js(SAMPLE)+'];',
]
for n in subs+normal:
    factory='languageModel' if n['type'].endswith('lmChatOpenAi') else 'tool' if n in subs else 'trigger' if n['type'].endswith(('manualTrigger','scheduleTrigger')) else 'node'
    cfg={k:v for k,v in n.items() if k not in ['id','type','typeVersion']}
    incoming=[(a,kind) for a,kinds in connections.items() for kind,outs in kinds.items() for out in outs for dest in out if dest['node']==n['name'] and kind!='main']
    models=[ids[a] for a,k in incoming if k=='ai_languageModel'];ts=[ids[a] for a,k in incoming if k=='ai_tool'];ss=[]
    if models:ss.append('model:'+models[0])
    if ts:ss.append('tools:['+','.join(ts)+']')
    cfgjs=js(cfg)
    if ss:cfgjs=cfgjs[:-1]+',subnodes:{'+','.join(ss)+'}}'
    sdk.append('const '+ids[n['name']]+' = '+factory+'({type:'+js(n['type'])+',version:'+js(n['typeVersion'])+',config:'+cfgjs+',output:sampleOutput});')
sdk.append("export default workflow('pv-wiki-official-datasheet-agent','PV Wiki - Official Datasheet Agent')")
for n in normal:sdk.append('  .add('+ids[n['name']]+')')
for a,kinds in connections.items():
    for i,outs in enumerate(kinds.get('main',[])):
        for dest in outs:sdk.append('  .add('+ids[a]+'.output('+str(i)+').to('+ids[dest['node']]+'))')
sdk[-1]+=';'
(HERE/'pv-wiki-datasheet-agent.sdk.js').write_text('\n'.join(sdk)+'\n',encoding='utf-8')
