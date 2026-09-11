from __future__ import annotations

import json
import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / "deploy/n8n/workflows/datasheet/pv-wiki-datasheet-agent.json"


class DatasheetWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.definition = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        cls.nodes = {node["name"]: node for node in cls.definition["nodes"]}
        cls.links = cls.definition["connections"]

    def destinations(self, name, branch=0, kind="main"):
        branches = self.links.get(name, {}).get(kind, [])
        return {edge["node"] for edge in branches[branch]} if len(branches) > branch else set()

    def reachable(self, start, target, blocked=()):
        visited, queue = set(), [start]
        while queue:
            name = queue.pop()
            if name == target:
                return True
            if name in visited:
                continue
            visited.add(name)
            for branch, edges in enumerate(self.links.get(name, {}).get("main", [])):
                if (name, branch) not in blocked:
                    queue.extend(edge["node"] for edge in edges)
        return False

    def code(self, name):
        return self.nodes[name]["parameters"]["jsCode"]

    def test_graph_is_complete_inactive_and_only_discovery_has_tools(self):
        self.assertFalse(self.definition["active"])
        self.assertEqual(len(self.nodes), len(self.definition["nodes"]))
        tool_targets = {}
        for source, kinds in self.links.items():
            self.assertIn(source, self.nodes)
            for kind, branches in kinds.items():
                for edges in branches:
                    for edge in edges:
                        self.assertIn(edge["node"], self.nodes)
                        if kind == "ai_tool":
                            tool_targets.setdefault(edge["node"], set()).add(source)
        self.assertEqual(tool_targets, {"Research Agent": {
            "Think", "web_search_exa", "web_search_advanced_exa", "web_fetch_exa",
        }})
        self.assertNotIn("pv_validate_research", self.nodes)

    def test_independent_audit_receives_real_header_and_page_images(self):
        for name in ("Extract Page Parameters", "Audit Page Parameters"):
            node = self.nodes[name]
            self.assertEqual(node["type"], "@n8n/n8n-nodes-langchain.chainLlm")
            images = [message for message in node["parameters"]["messages"]["messageValues"]
                      if message.get("messageType") == "imageBinary"]
            self.assertEqual({"page"}, {item["binaryImageDataKey"] for item in images})
            self.assertTrue(all(item["imageDetail"] == "high" for item in images))
        self.assertNotEqual(self.nodes["Extract Page Parameters"]["id"], self.nodes["Audit Page Parameters"]["id"])
        for name in ("Attach Extraction Images", "Attach Audit Images"):
            self.assertIn("data:image.data", self.code(name))
            self.assertIn("state.current.page", self.code(name))
            self.assertNotIn("binary.header", self.code(name))
        for name in ("Render Extraction Pages", "Render Audit Pages"):
            self.assertIn("pages:[$json.current.page]", self.nodes[name]["parameters"]["jsonBody"])
            self.assertNotIn("current.header_page", self.nodes[name]["parameters"]["jsonBody"])
        self.assertEqual({"Apply Page Audit"}, self.destinations("Audit Page Parameters"))
        self.assertEqual({"Apply Page Audit"}, self.destinations("Audit Text Parameters"))
        self.assertEqual({"Audit Text Parameters"}, self.destinations("Audit Images Available", 1))
        self.assertIn("region:$json.current.region", self.nodes["Render Audit Crop"]["parameters"]["jsonBody"])
        for name in ("Locate Datasheet Pages", "Extract Text Parameters", "Extract Page Parameters",
                     "Audit Text Parameters", "Audit Page Parameters"):
            prompt = self.nodes[name]["parameters"]["messages"]["messageValues"][0]["message"]
            self.assertIn("series-cover maximum", prompt)
            self.assertIn("constant", prompt)
            self.assertIn("model names", prompt)
            self.assertIn("non-numeric features", prompt)

    def test_models_receive_narrow_page_or_translation_inputs(self):
        fields = {"Research Agent": "discovery_prompt", "Locate Datasheet Pages": "plan_prompt",
                  "Extract Text Parameters": "page_prompt", "Extract Page Parameters": "page_prompt",
                  "Audit Text Parameters": "audit_prompt", "Audit Page Parameters": "audit_prompt"}
        for name, field in fields.items():
            self.assertEqual(f"={{{{ JSON.stringify($json.{field}) }}}}", self.nodes[name]["parameters"]["text"])
        audit = self.nodes["Audit Translations"]["parameters"]["text"]
        self.assertIn("parameters:$json.decision.parameters", audit)
        self.assertNotIn("receipt", audit)
        self.assertNotIn("JSON.stringify($json)", audit)
        self.assertEqual({"Translate Parameters"}, self.destinations("Prepare Translation Batches"))
        self.assertIn("parameters:rows.slice(i*25,(i+1)*25)", self.code("Prepare Translation Batches"))
        self.assertNotIn("receipt", self.code("Prepare Translation Batches"))

    def test_discovery_prioritizes_matching_english_sources_and_records_fallback(self):
        prompt = self.nodes["Research Agent"]["parameters"]["options"]["systemMessage"]
        self.assertIn("First search the official website", prompt)
        self.assertIn("Exact model/market/version applicability has higher priority than language", prompt)
        self.assertIn("never guess an English URL", prompt)
        self.assertIn("code:invalid_tool_arguments", prompt)
        self.assertIn("before any paid request", prompt)
        self.assertIn("runtime failure ends this attempt", prompt)
        for field in ("datasheet_language", "language_selection_reason"):
            self.assertIn(field, prompt)
            self.assertIn(field, self.code("Read Discovery"))
            self.assertIn(field, self.code("Build Decision"))
        self.assertIn("preferred_datasheet_language:'en'", self.code("Discovery Input"))
        self.assertIn("known_datasheet_urls", prompt)
        self.assertIn("untrusted leads", prompt)
        self.assertIn("datasheet_hints:{}", self.code("Configuration"))
        self.assertIn("config.datasheet_hints?.[selected.product_id]", self.code("Initialize Research"))
        self.assertIn("without a separate brand/company prefix", prompt)
        self.assertIn("discoveryModel", self.code("Read Discovery"))
        self.assertIn("pdfModelMismatch", self.code("Initialize Research"))
        self.assertIn("restored.pdf.receipt?.target_model", self.code("Initialize Research"))
        self.assertIn("repairPageContext(state)", self.code("Initialize Research"))
        self.assertIn("suggestion.header_page<=page", self.code("Read Page Plan"))
        self.assertIn("headerReference(state,headerPage,page)", self.code("Prepare Validation Repair"))

    def test_exa_tool_descriptions_expose_distinct_argument_schemas(self):
        for name in ("web_search_exa", "web_search_advanced_exa", "web_fetch_exa"):
            description = self.nodes[name]["parameters"]["toolDescription"]
            self.assertIn("JSON object", description)
            self.assertNotEqual(description, "Makes an HTTP request and returns response data")
        self.assertIn("query:string", self.nodes["web_search_exa"]["parameters"]["toolDescription"])
        self.assertIn("includeDomains", self.nodes["web_search_advanced_exa"]["parameters"]["toolDescription"])
        fetch = self.nodes["web_fetch_exa"]["parameters"]
        self.assertIn("urls:string[]", fetch["toolDescription"])
        self.assertIn('"urls":["https://manufacturer.example/product"]', fetch["toolDescription"])
        self.assertIn("not a singular url field", fetch["jsonBody"])

    def test_optional_translation_and_independent_audit_cover_full_bilingual_fields(self):
        for name in ("Translate Parameters", "Audit Translations"):
            prompt = self.nodes[name]["parameters"]["messages"]["messageValues"][0]["message"]
            for field in ("value_zh", "conditions_zh", "footnotes_zh"):
                self.assertIn(field, prompt)
            self.assertIn("order", prompt)
            self.assertIn("certification", prompt)
            self.assertIn("empty string", prompt)
            self.assertIn("do not generate product analysis", prompt)
            self.assertNotIn("analysis_zh:", prompt)
        for name in ("Merge Translations", "Read Translation Audit"):
            self.assertIn("cleanTranslations", self.code(name))
            self.assertNotRegex(self.code(name), r"decision\.parameters\s*=")
        audit = self.nodes["Audit Translations"]["parameters"]["text"]
        self.assertIn("datasheet_language", audit)
        self.assertIn("if(response.error)throw", self.code("Read Translation Audit"))
        self.assertIn("if(answer.error)throw", self.code("Read Translation Audit"))

    def test_translation_stages_replace_product_analysis_without_changing_model_routes(self):
        for old_name in ("Prepare Analysis Batches", "Optional Analysis and Translation",
                         "Merge Optional Analysis", "Audit Optional Analysis",
                         "Read Analysis Audit", "Retry Without Analysis"):
            self.assertNotIn(old_name, self.nodes)
        for name in ("Translate Parameters", "Audit Translations"):
            self.assertIn(name, self.destinations("Page Research Model", kind="ai_languageModel"))
        self.assertEqual({"Audit Translations"}, self.destinations("Merge Translations"))
        self.assertEqual({"Read Translation Audit"}, self.destinations("Audit Translations"))
        self.assertEqual({"Validate Candidate"}, self.destinations("Read Translation Audit"))
        audit = self.nodes["Audit Translations"]["parameters"]["text"]
        self.assertIn("translations:$json.proposed_translations.translations", audit)
        self.assertNotIn("analysis:", audit)

    def test_transport_injects_claim_revision_and_only_exa_uses_ai_payloads(self):
        claimers = [node for node in self.nodes.values()
                    if "/datasheet/claim" in node["parameters"].get("url", "")]
        self.assertEqual(len(claimers), 1)
        self.assertIn("expected_revision:state.selected.revision", self.code("Build Decision"))
        ai_payload_nodes = set()
        for name, node in self.nodes.items():
            parameters = node["parameters"]
            if "$fromAI" in json.dumps(parameters):
                ai_payload_nodes.add(name)
                self.assertIn("$fromAI('tool_input',", parameters["jsonBody"])
                self.assertNotIn("$fromAI('arguments'", parameters["jsonBody"])
            if node["type"] not in ("n8n-nodes-base.httpRequest", "n8n-nodes-base.httpRequestTool"):
                continue
            self.assertEqual("httpHeaderAuth", parameters["genericAuthType"])
            self.assertNotIn("retryOnFail", node)
            if parameters.get("sendBody"):
                self.assertEqual(parameters["specifyBody"], "json")
            if "/datasheet/tools/" in parameters["url"] and name != "Read Research Result":
                headers = parameters["headerParameters"]["parameters"]
                self.assertTrue(any(item["name"] == "X-PV-Wiki-Lease" for item in headers))
        self.assertEqual(ai_payload_nodes, {"web_search_exa", "web_search_advanced_exa", "web_fetch_exa"})
        for name in ("Validate Candidate", "Validate Parameters Only", "Save Research"):
            body = self.nodes[name]["parameters"]["jsonBody"]
            for field in ("product_id:$json.selected.product_id", "source_hash:$json.selected.source_hash",
                          "decision:$json.decision", "evidence_documents:$json.evidence_documents"):
                self.assertIn(field, body)

    def test_checkpoint_and_repair_loop_return_to_independent_audit(self):
        checkpoint = self.nodes["Checkpoint Progress"]
        self.assertEqual(checkpoint["type"], "n8n-nodes-base.httpRequest")
        self.assertIn("context:$json", checkpoint["parameters"]["jsonBody"])
        for name in ("Read Page Draft", "Apply Page Audit"):
            self.assertEqual({"Progress Snapshot"}, self.destinations(name))
        self.assertEqual({"Checkpoint Progress"}, self.destinations("Progress Snapshot"))
        self.assertEqual({"Restore Progress"}, self.destinations("Checkpoint Progress"))
        self.assertEqual({"Prepare Audit"}, self.destinations("Page Review Finished", 1))
        self.assertTrue(self.reachable("Restore Progress", "Prepare Audit"))
        self.assertIn("state.current.done?'page_done':'audit'", self.code("Apply Page Audit"))
        self.assertEqual({"Prepare Validation Repair"}, self.destinations("Validation Passed", 1))
        self.assertEqual({"Progress Snapshot"}, self.destinations("Validation Repair Allowed"))
        self.assertIn("state.next_action='audit'", self.code("Prepare Validation Repair"))
        self.assertEqual({"Prepare Result"}, self.destinations("Continue Research", 1))

    def test_save_requires_successful_validation_and_explicit_save_gate(self):
        self.assertTrue(self.reachable("Manual Research", "Save Research"))
        self.assertFalse(self.reachable("Manual Research", "Save Research", blocked={("Validation Passed", 0)}))
        self.assertFalse(self.reachable("Manual Research", "Save Research", blocked={("Save Enabled", 0)}))
        self.assertEqual({"Prepare Save"}, self.destinations("Validation Passed"))
        self.assertEqual({"Save Research"}, self.destinations("Save Enabled"))
        self.assertEqual({"Prepare Result"}, self.destinations("Save Enabled", 1))
        self.assertEqual({"Publish New Research"}, self.destinations("Publish Enabled"))
        self.assertIn("state.saved&&state.publication_configured===true", self.code("Read Save Result"))

    def test_translation_is_nonblocking_and_results_follow_release(self):
        for name in ("Translate Parameters", "Audit Translations"):
            self.assertEqual("continueRegularOutput", self.nodes[name]["onError"])
        for name in ("Merge Translations", "Read Translation Audit", "Read Validation"):
            self.assertNotRegex(self.code(name), r"decision\.parameters\s*=")
        self.assertIn("delete state.decision.analysis", self.code("Read Validation"))
        self.assertEqual({"Validate Parameters Only"}, self.destinations("Retry Without Translations"))
        self.assertEqual({"Validation Passed"}, self.destinations("Retry Without Translations", 1))
        self.assertEqual({"Release Research Session"}, self.destinations("Format Result"))
        self.assertEqual({"Research Result"}, self.destinations("Release Research Session"))
        self.assertIn("'publishable'", self.code("Format Result"))
        self.assertIn("'incomplete'", self.code("Format Result"))
        self.assertIn("session_released", self.code("Research Result"))
        self.assertTrue(self.reachable("Unexpected Failure", "Research Result"))


if __name__ == "__main__":
    unittest.main()
