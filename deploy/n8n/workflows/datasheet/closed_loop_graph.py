"""Build the native n8n page-extraction, independent-review and repair graph."""

import json

from closed_loop_nodes import COMMON, NODES
from closed_loop_prompts import TRANSLATION, TRANSLATION_AUDIT, AUDIT, DISCOVERY, EXTRACT, PLAN


def configure(add, link, code, gate, http, chain, agent, here, nodes):
    def runtime(name):
        review = (here / 'closed_loop_review.js').read_text(encoding='utf-8') if name == 'Apply Page Audit' else ''
        return code(name, COMMON + review + '\n' + NODES[name])

    def llm(name, prompt, expression, visual=False):
        messages = [dict(type='SystemMessagePromptTemplate', message=prompt)]
        if visual:
            messages += [dict(type='HumanMessagePromptTemplate', messageType='imageBinary',
                              binaryImageDataKey=key, imageDetail='high') for key in ('page',)]
        add(name, '@n8n/n8n-nodes-langchain.chainLlm', 1.9,
            dict(promptType='define', text=expression, messages=dict(messageValues=messages),
                 batching=dict(batchSize=1)), onError='continueRegularOutput')
        link('Page Vision Model' if visual else 'Page Research Model', name, kind='ai_languageModel')

    def path(*names):
        for a, b in zip(names, names[1:]):
            link(a, b)

    args = '={{ {product_id:$json.selected.product_id,source_hash:$json.selected.source_hash,decision:$json.decision,evidence_documents:$json.evidence_documents} }}'
    views = '={{ {document_id:$json.pdf.document_id,pages:[$json.current.page]} }}'

    add('Manual Research', 'n8n-nodes-base.manualTrigger', 1)
    add('Every 30 Minutes', 'n8n-nodes-base.scheduleTrigger', 1.2,
        dict(rule=dict(interval=[dict(field='minutes', minutesInterval=30)])))
    code('Configuration', "return [{json:{worker_url:'http://pv-wiki-worker:8080',vision_enabled:true,max_review_rounds:5,save_results:true,datasheet_hints:{}}}];")
    http('Claim One Item', '/datasheet/claim', '={{ {execution_id:String($execution.id)} }}', lease=False, soft=True)
    gate('Claim Acquired', '$json.ok === true && $json.acquired === true')
    runtime('No Work Result')
    gate('Pending Publication', "$json.work?.kind === 'publication'")
    gate('Publication Configured', '$json.publication_configured === true')
    http('Publish Pending', '/datasheet/tools/pv_publish_result', '={{ {product_id:$json.work.completion.product_id} }}', soft=True)
    http('Read Research Contract', '/datasheet/contract', method='GET', soft=True)
    runtime('Initialize Research')
    gate('Cached PDF Available', '!!$json.pdf?.document_id && Array.isArray($json.pdf.page_texts)')
    runtime('Discovery Input')
    add('Research Agent', '@n8n/n8n-nodes-langchain.agent', 3.1,
        dict(promptType='define', text='={{ JSON.stringify($json.discovery_prompt) }}',
             options=dict(systemMessage=DISCOVERY, maxIterations=16, returnIntermediateSteps=False,
                          passthroughBinaryImages=False)), onError='continueRegularOutput')
    for name, description, tool_description in [
        ('web_search_exa',
         'JSON object {"query":"exact manufacturer and model English datasheet","numResults":5}. query is a required nonempty string; numResults is an optional integer from 1 to 100.',
         'Search the web to discover official manufacturer product pages and English PDF datasheets. Supply tool_input as a JSON object with required query:string and optional numResults:integer (1-100). Example shape: {"query":"manufacturer exact-model English datasheet","numResults":5}. Use actual catalogue terms. To fetch an already discovered page URL, use web_fetch_exa instead.'),
        ('web_search_advanced_exa',
         'JSON object with required query:string; optional numResults:integer (1-100), includeDomains:string[], excludeDomains:string[], includeText:string[], excludeText:string[].',
         'Search with domain/text filters to locate an exact official product or datasheet, returning extracted search content. Supply tool_input as a JSON object: query is a required nonempty string; numResults is an optional integer (1-100); includeDomains, excludeDomains, includeText and excludeText are optional arrays of strings. Example shape: {"query":"exact-model English datasheet","includeDomains":["manufacturer.example"],"numResults":5}. Replace the example domain with a verified manufacturer domain. This searches; it does not fetch a supplied URL.'),
        ('web_fetch_exa',
         'JSON object {"urls":["https://manufacturer.example/product"],"maxCharacters":6000}. urls is a required nonempty array of actual discovered URL strings, not a singular url field. maxCharacters is an optional integer from 1 to 200000.',
         'Fetch content from already discovered official product/download pages to inspect PDF links and language/model applicability. Supply tool_input as a JSON object with required urls:string[] (a nonempty array, even for one URL) and optional maxCharacters:integer (1-200000). Example shape: {"urls":["https://manufacturer.example/product"],"maxCharacters":6000}. Use real discovered URLs, not this example. Do not send a query or a singular url field; use web_search_exa to discover URLs.'),
    ]:
        http(name, '/datasheet/tools/' + name,
             "={{ $fromAI('tool_input'," + json.dumps(description) + ",'json') }}", tool=True)
        nodes[-1]['parameters']['toolDescription'] = tool_description
        link(name, 'Research Agent', kind='ai_tool')
    add('Think', '@n8n/n8n-nodes-langchain.toolThink', 1.1,
        dict(description='Record only a brief manufacturer, exact-model/market/version, official-source, English-datasheet search/fallback and next-action checklist.'))
    link('Think', 'Research Agent', kind='ai_tool')
    runtime('Read Discovery')
    gate('Official Datasheet Found', '$json.found === true')
    http('Read Original PDF', '/datasheet/tools/read_datasheet_pdf',
         '={{ {url:$json.discovery.url,target_model:$json.discovery.target_model,official_page_url:$json.discovery.official_page_url||null} }}', soft=True)
    runtime('Read PDF Result')
    gate('PDF Available', '$json.pdf_available === true')
    runtime('Prepare Page Plan')
    gate('Page Plan Available', '$json.plan_ready === true')
    llm('Locate Datasheet Pages', PLAN, '={{ JSON.stringify($json.plan_prompt) }}')
    runtime('Read Page Plan')
    runtime('Choose Page')
    gate('Page Available', '$json.has_page === true')
    gate('Extraction Needed', '$json.extraction_needed === true')
    gate('Complete Page Text', '!!$json.current.plain_text && !$json.current.text_truncated')
    gate('Extraction With Vision', '$json.config.vision_enabled === true')
    http('Render Extraction Pages', '/datasheet/tools/view_datasheet_pages', views, soft=True)
    runtime('Attach Extraction Images')
    gate('Extraction Images Available', '$json.extraction_has_images === true')
    llm('Extract Text Parameters', EXTRACT, '={{ JSON.stringify($json.page_prompt) }}')
    llm('Extract Page Parameters', EXTRACT, '={{ JSON.stringify($json.page_prompt) }}', visual=True)
    runtime('Read Page Draft')
    runtime('Prepare Audit')
    gate('Audit With Vision', '$json.config.vision_enabled === true')
    http('Render Audit Pages', '/datasheet/tools/view_datasheet_pages', views, soft=True)
    runtime('Attach Audit Images')
    gate('Audit Images Available', '$json.audit_has_images === true')
    gate('Audit Crop Needed', '$json.needs_crop === true')
    http('Render Audit Crop', '/datasheet/tools/view_datasheet_pages',
         '={{ {document_id:$json.pdf.document_id,pages:[$json.current.page],region:$json.current.region} }}', soft=True)
    runtime('Attach Audit Crop')
    llm('Audit Page Parameters', AUDIT, '={{ JSON.stringify($json.audit_prompt) }}', visual=True)
    llm('Audit Text Parameters', AUDIT, '={{ JSON.stringify($json.audit_prompt) }}')
    runtime('Apply Page Audit')
    runtime('Finish Page')
    runtime('Progress Snapshot')
    http('Checkpoint Progress', '/datasheet/tools/pv_checkpoint_research',
         '={{ {product_id:$json.selected.product_id,source_hash:$json.selected.source_hash,stage:$json.checkpoint_stage,context:$json} }}', soft=True)
    runtime('Restore Progress')
    gate('Continue Research', "$json.next_action !== 'stop'")
    gate('Plan Next', "$json.next_action === 'plan'")
    gate('Choose Next Page', "$json.next_action === 'choose_page'")
    gate('Page Review Finished', "$json.next_action === 'page_done'")
    runtime('Build Decision')
    gate('Confirmed Parameters Available', '$json.has_parameters === true')
    runtime('Prepare Translation Batches')
    llm('Translate Parameters', TRANSLATION, '={{ JSON.stringify($json) }}')
    runtime('Merge Translations')
    llm('Audit Translations', TRANSLATION_AUDIT,
        '={{ JSON.stringify({model:$json.decision.model,datasheet_language:$json.decision.official_source?.datasheet_language||"unknown",parameters:$json.decision.parameters,translations:$json.proposed_translations.translations}) }}')
    runtime('Read Translation Audit')
    http('Validate Candidate', '/datasheet/tools/pv_validate_research', args, soft=True)
    runtime('Read Validation')
    gate('Retry Without Translations', '$json.retry_without_translations === true')
    http('Validate Parameters Only', '/datasheet/tools/pv_validate_research', args, soft=True)
    runtime('Read Parameter Validation')
    gate('Validation Passed', '$json.validated === true')
    runtime('Prepare Validation Repair')
    gate('Validation Repair Allowed', '$json.validation_repair_allowed === true')
    runtime('Prepare Save')
    gate('Save Enabled', '$json.save_enabled === true')
    http('Save Research', '/datasheet/tools/pv_save_research', args, soft=True)
    runtime('Read Save Result')
    gate('Publish Enabled', '$json.publish_enabled === true')
    http('Publish New Research', '/datasheet/tools/pv_publish_result', '={{ {product_id:$json.selected.product_id} }}', soft=True)
    runtime('Keep Published Context')
    runtime('Prepare Result')
    http('Read Research Result', '/datasheet/tools/pv_research_result', '={{ {product_id:$json.selected.product_id} }}', lease=False, soft=True)
    runtime('Format Result')
    http('Release Research Session', '/datasheet/release', "={{ {lease_token:$('Claim One Item').first().json.lease_token} }}", lease=False, soft=True)
    runtime('Research Result')
    code('Unexpected Failure', COMMON + "const claim=$('Claim One Item').first().json;return result({selected:claim.work?.product||{product_id:claim.work?.completion?.product_id},error:{stage:'workflow',message:errText($input.first().json)||'Unexpected workflow error'}});")

    for name, model, structured in [
        ('Research Chat Model', 'REPLACE_WITH_YOUR_MODEL_ID', False),
        ('Page Research Model', 'REPLACE_WITH_YOUR_MODEL_ID', True),
        ('Page Vision Model', 'REPLACE_WITH_YOUR_VISION_MODEL_ID', True),
    ]:
        options = dict(timeout=300000, maxRetries=0)
        if structured:
            options.update(responseFormat='json_object', maxTokens=32768, temperature=0)
        add(name, '@n8n/n8n-nodes-langchain.lmChatOpenAi', 1.3,
            dict(model=dict(__rl=True, mode='id', value=model), responsesApiEnabled=False, options=options),
            onError='continueRegularOutput', notes='Reuse the existing research/vision provider credential and selected model. Research and all model calls remain in n8n.')
    link('Research Chat Model', 'Research Agent', kind='ai_languageModel')

    path('Manual Research', 'Configuration', 'Claim One Item', 'Claim Acquired')
    link('Every 30 Minutes', 'Configuration')
    link('Claim Acquired', 'No Work Result', 1)
    path('Claim Acquired', 'Pending Publication', 'Publication Configured', 'Publish Pending', 'Prepare Result')
    link('Publication Configured', 'Prepare Result', 1)
    link('Pending Publication', 'Read Research Contract', 1)
    path('Read Research Contract', 'Initialize Research', 'Cached PDF Available', 'Prepare Page Plan')
    link('Cached PDF Available', 'Discovery Input', 1)
    path('Discovery Input', 'Research Agent', 'Read Discovery', 'Official Datasheet Found', 'Read Original PDF', 'Read PDF Result', 'PDF Available', 'Progress Snapshot')
    link('Official Datasheet Found', 'Prepare Result', 1)
    link('PDF Available', 'Prepare Result', 1)
    path('Prepare Page Plan', 'Page Plan Available', 'Choose Page')
    link('Page Plan Available', 'Locate Datasheet Pages', 1)
    path('Locate Datasheet Pages', 'Read Page Plan', 'Progress Snapshot')
    path('Choose Page', 'Page Available', 'Extraction Needed', 'Complete Page Text', 'Extract Text Parameters', 'Read Page Draft', 'Progress Snapshot')
    link('Page Available', 'Build Decision', 1)
    link('Extraction Needed', 'Prepare Audit', 1)
    link('Complete Page Text', 'Extraction With Vision', 1)
    path('Extraction With Vision', 'Render Extraction Pages', 'Attach Extraction Images', 'Extraction Images Available', 'Extract Page Parameters', 'Read Page Draft')
    link('Extraction With Vision', 'Extract Text Parameters', 1)
    link('Extraction Images Available', 'Extract Text Parameters', 1)
    path('Prepare Audit', 'Audit With Vision', 'Render Audit Pages', 'Attach Audit Images', 'Audit Images Available', 'Audit Crop Needed', 'Render Audit Crop', 'Attach Audit Crop', 'Audit Page Parameters', 'Apply Page Audit', 'Progress Snapshot')
    link('Audit With Vision', 'Audit Text Parameters', 1)
    link('Audit Images Available', 'Audit Text Parameters', 1)
    link('Audit Crop Needed', 'Audit Page Parameters', 1)
    link('Audit Text Parameters', 'Apply Page Audit')
    path('Progress Snapshot', 'Checkpoint Progress', 'Restore Progress', 'Continue Research', 'Plan Next', 'Prepare Page Plan')
    link('Continue Research', 'Prepare Result', 1)
    link('Plan Next', 'Choose Next Page', 1)
    link('Choose Next Page', 'Choose Page')
    link('Choose Next Page', 'Page Review Finished', 1)
    path('Page Review Finished', 'Finish Page', 'Progress Snapshot')
    link('Page Review Finished', 'Prepare Audit', 1)
    path('Build Decision', 'Confirmed Parameters Available', 'Prepare Translation Batches', 'Translate Parameters', 'Merge Translations', 'Audit Translations', 'Read Translation Audit', 'Validate Candidate', 'Read Validation', 'Retry Without Translations', 'Validate Parameters Only', 'Read Parameter Validation', 'Validation Passed', 'Prepare Save', 'Save Enabled', 'Save Research', 'Read Save Result', 'Publish Enabled', 'Publish New Research', 'Keep Published Context', 'Prepare Result')
    link('Confirmed Parameters Available', 'Prepare Result', 1)
    link('Retry Without Translations', 'Validation Passed', 1)
    link('Validation Passed', 'Prepare Validation Repair', 1)
    path('Prepare Validation Repair', 'Validation Repair Allowed', 'Progress Snapshot')
    link('Validation Repair Allowed', 'Prepare Result', 1)
    link('Save Enabled', 'Prepare Result', 1)
    link('Publish Enabled', 'Prepare Result', 1)
    path('Prepare Result', 'Read Research Result', 'Format Result', 'Release Research Session', 'Research Result')
    path('Unexpected Failure', 'Prepare Result')
    for item in nodes:
        if item.get('onError') == 'continueErrorOutput' and item['name'] not in ('Configuration', 'Unexpected Failure', 'Research Result', 'No Work Result'):
            link(item['name'], 'Unexpected Failure', 1)
