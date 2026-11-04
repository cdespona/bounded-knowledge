"""Guards on the Conductor workflows: they sequence landscape commands and nothing else."""

from pathlib import Path
import re
import unittest

WORKFLOWS = Path(__file__).resolve().parents[2] / "workflows"


class WorkflowTests(unittest.TestCase):
    def files(self):
        found = sorted(WORKFLOWS.glob("*.yaml"))
        self.assertTrue(found)
        return found

    def test_script_steps_only_run_landscape_commands(self):
        for path in self.files():
            text = path.read_text(encoding="utf-8")
            for command in re.findall(r"^\s+command:\s*(\S+)", text, re.M):
                self.assertIn(command, {"./tools/landscape", "sh"}, path.name)
            for shell in re.findall(r"args: \[-c, '([^']*)'", text):
                self.assertTrue(shell.startswith("exec ./tools/landscape "), "{}: {}".format(path.name, shell))

    def test_workflows_name_no_broad_copilot_permissions_or_agents_directly(self):
        for path in self.files():
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"--allow|--yolo|provider:|type:\s*agent", path.name)

    def test_the_approval_gate_offers_the_safe_option_first(self):
        text = (WORKFLOWS / "promotion.yaml").read_text(encoding="utf-8")
        options = re.findall(r"^\s+- label: .*\n\s+value: (\w+)", text, re.M)
        self.assertEqual(options[0], "reject")


if __name__ == "__main__":
    unittest.main()
