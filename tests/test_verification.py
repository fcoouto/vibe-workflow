"""Loading proof must require an active native registration, not an early snapshot."""
import unittest
from pathlib import Path

from tests.verify_native_loading import inspect_result


class LoadingProofTest(unittest.TestCase):
    export = Path("/synthetic/export")

    def response(self, status="active", tools=None):
        return {"output": {
            "tools": ["smart_router_select", "vibe_workflow_onboard"] if tools is None else tools,
            "plugins": {"data": [{
                "id": "vibe-workflow.smart-router",
                "state": {"status": status},
                "source": {"type": "local", "path": "/synthetic/export"},
            }]},
            "skill": {"name": "vibe-onboard-project", "path": str(self.export / "skills/vibe-onboard-project/SKILL.md"),
                      "content": "[procedure](references/procedure.md)"},
        }}

    def test_rpc_output_envelope_requires_the_selector_tool(self):
        self.assertEqual(inspect_result(self.response(), "synthetic", self.export)["state"], "passed")
        self.assertEqual(inspect_result(self.response(tools=["smart_router_select"]), "synthetic", self.export)["state"], "failed")

    def test_inactive_plugin_does_not_count_as_loaded(self):
        self.assertEqual(inspect_result(self.response(status="failed"), "synthetic", self.export)["state"], "failed")

    def test_skill_from_another_install_does_not_count(self):
        response = self.response()
        response["output"]["skill"]["path"] = "/unrelated/installed/SKILL.md"
        self.assertEqual(inspect_result(response, "synthetic", self.export)["reason"], "bundled_skill_not_registered")


if __name__ == "__main__":
    unittest.main()
