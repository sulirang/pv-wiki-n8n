from __future__ import annotations
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "deploy/n8n/workflows/datasheet/pv-wiki-datasheet-agent.json"

class DatasheetWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
        cls.nodes = {item["name"]: item for item in cls.workflow["nodes"]}
        cls.links = cls.workflow["connections"]

    def test_inactive_workflow_owns_research_in_n8n(self):
        self.assertFalse(self.workflow["active"])
        serialized = json.dumps(self.workflow)
        for forbidden in ("/run-one", "executeCommand", "$env", "hermes cron"):
            self.assertNotIn(forbidden, serialized)
        for node in self.nodes.values():
            self.assertNotIn("credentials", node)
        interval = self.nodes["Every 30 Minutes"]["parameters"]["rule"]["interval"][0]
        self.assertEqual({"field": "minutes", "minutesInterval": 30}, interval)
        for model in ("Research Chat Model", "Vision Chat Model"):
            self.assertTrue(self.nodes[model]["parameters"]["model"]["value"].startswith("REPLACE_WITH_"))
        research_targets = {edge["node"] for edge in self.links["Research Chat Model"]["ai_languageModel"][0]}
        self.assertTrue({"Research Agent", "Compose Parameters Agent", "Self Review"}.issubset(research_targets))

    def test_agent_is_not_given_state_mutation_or_catalogue_tools(self):
        targets = {}
        for source, kinds in self.links.items():
            for group in kinds.get("ai_tool", []):
                for edge in group:
                    targets.setdefault(edge["node"], set()).add(source)
        self.assertIn("Think", targets["Research Agent"])
        self.assertIn("web_search_exa", targets["Research Agent"])
        for values in targets.values():
            self.assertFalse({"Save Research", "Publish New Research", "Claim One Item"}.intersection(values))
        self.assertNotIn("pv_refresh_catalogue", str(targets))

    def test_vision_images_use_real_binary_channels(self):
        messages = self.nodes["Visual Datasheet Check"]["parameters"]["messages"]["messageValues"]
        images = [item for item in messages if item.get("messageType") == "imageBinary"]
        self.assertEqual({"page", "previous", "anchor"}, {item["binaryImageDataKey"] for item in images})
        self.assertTrue(all(item["imageDetail"] == "high" for item in images))
        code = self.nodes["Attach Real Images"]["parameters"]["jsCode"]
        self.assertIn("binary", code)
        self.assertIn("image.data", code)
        self.assertNotIn("base64", self.nodes["Visual Datasheet Check"]["parameters"]["text"])
        self.assertEqual("Capture Visual Review", self.links["Visual Datasheet Check"]["main"][0][0]["node"])

    def test_single_claim_and_bound_tool_endpoints(self):
        claimers = [item for item in self.nodes.values()
                    if "/datasheet/claim" in str(item["parameters"].get("url", ""))]
        self.assertEqual(1, len(claimers))
        for item in self.nodes.values():
            if item["type"] not in ("n8n-nodes-base.httpRequest", "n8n-nodes-base.httpRequestTool"):
                continue
            p = item["parameters"]
            self.assertEqual("httpHeaderAuth", p["genericAuthType"])
            self.assertNotIn("retryOnFail", item)
            if "/datasheet/tools/" in p["url"]:
                headers = p["headerParameters"]["parameters"]
                self.assertTrue(any(h["name"] == "X-PV-Wiki-Lease" for h in headers))
        self.assertEqual("Publish Pending", self.links["Pending Publication"]["main"][0][0]["node"])
        self.assertEqual("Release Research Session", self.links["Publish Pending"]["main"][0][0]["node"])

    def test_repairs_validate_again_and_never_replay_blind_save(self):
        for source in ("Candidate Ready", "Self Review Passed", "Validation Passed", "Research Saved"):
            self.assertEqual("Prepare Retry", self.links[source]["main"][1][0]["node"])
        self.assertEqual("Compose Input", self.links["Needs More Images"]["main"][1][0]["node"])
        self.assertEqual("Discovery Input", self.links["Needs More Search"]["main"][0][0]["node"])
        self.assertEqual("Prepare Page Reviews", self.links["Needs More Images"]["main"][0][0]["node"])
        retry = self.nodes["Prepare Retry"]["parameters"]["jsCode"]
        self.assertIn("max_repair_attempts", retry)
        self.assertIn("max_research_rounds", retry)
        self.assertEqual("Save Research", self.links["Validation Passed"]["main"][0][0]["node"])
        self.assertEqual("Publish New Research", self.links["Research Saved"]["main"][0][0]["node"])
        self.assertIn("restart_page_loop", self.nodes["Review Pages Serially"]["parameters"]["options"]["reset"])

    def test_failures_release_and_optional_analysis_is_nonblocking(self):
        for item in self.nodes.values():
            if item.get("onError") != "continueErrorOutput" or item["name"] in {"Claim One Item", "Configuration"}:
                continue
            self.assertEqual("Release Research Session", self.links[item["name"]]["main"][1][0]["node"])
        self.assertEqual("continueRegularOutput", self.nodes["Optional Analysis and Translation"]["onError"])
        merge = self.nodes["Merge Optional Enrichment"]["parameters"]["jsCode"]
        self.assertIn("catch", merge)
        self.assertNotRegex(merge, r"parameters\s*=")
        for item in self.nodes.values():
            code = item["parameters"].get("jsCode", "")
            self.assertNotRegex(code, r"slice\s*\(\s*0\s*,\s*200")
            self.assertNotIn(".last().json", code)

if __name__ == "__main__":
    unittest.main()
