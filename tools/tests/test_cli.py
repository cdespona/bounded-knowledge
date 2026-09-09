"""Subprocess-level acceptance tests for the supported CLI contract."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import sys


TOOLS = Path(__file__).resolve().parents[1]
PROJECT = TOOLS.parent
LANDSCAPE = TOOLS / "landscape"
sys.path.insert(0, str(TOOLS))

from landscape_core.contracts import validate_evidence_bundle  # noqa: E402


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


class LandscapeCliTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.base = Path(self.temporary_directory.name)
        source = PROJECT / "examples" / "synthetic-java-service"
        self.repository = self.base / "synthetic-java-service"
        shutil.copytree(str(source), str(self.repository))
        git(self.repository, "init", "-q")
        git(self.repository, "config", "user.name", "Mercurio Test")
        git(self.repository, "config", "user.email", "mercurio@example.invalid")
        git(self.repository, "add", ".")
        git(self.repository, "commit", "-q", "-m", "Synthetic fixture")

    def run_cli(self, *arguments):
        return subprocess.run(
            [str(LANDSCAPE), *map(str, arguments)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_help_contract(self):
        result = self.run_cli("--help")
        self.assertEqual(0, result.returncode)
        self.assertIn("{preflight,discover,validate,status,sources,evidence}", result.stdout)
        self.assertEqual("", result.stderr)

    def test_argument_failure_exit_contract(self):
        result = self.run_cli("discover", self.repository)
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("usage: landscape discover", result.stderr)

    def test_preflight_success_and_dirty_exit_contract(self):
        clean = self.run_cli("preflight", self.repository)
        self.assertEqual(0, clean.returncode)
        self.assertTrue(json.loads(clean.stdout)["clean"])
        self.assertEqual("", clean.stderr)

        (self.repository / "README.md").write_text("dirty\n", encoding="utf-8")
        dirty = self.run_cli("preflight", self.repository)
        self.assertEqual(2, dirty.returncode)
        self.assertFalse(json.loads(dirty.stdout)["clean"])
        self.assertEqual("", dirty.stderr)

    def test_discover_stdout_and_output_contract(self):
        stdout_result = self.run_cli(
            "discover", self.repository, "--repository", "synthetic-java-service"
        )
        self.assertEqual(0, stdout_result.returncode)
        inventory = json.loads(stdout_result.stdout)
        self.assertEqual("synthetic-java-service", inventory["repository"])
        self.assertEqual("", stdout_result.stderr)
        repeated = self.run_cli(
            "discover", self.repository, "--repository", "synthetic-java-service"
        )
        self.assertEqual(stdout_result.stdout, repeated.stdout)

        output = self.base / "nested" / "inventory.json"
        file_result = self.run_cli(
            "discover",
            self.repository,
            "--repository",
            "synthetic-java-service",
            "--output",
            output,
        )
        self.assertEqual(0, file_result.returncode)
        self.assertEqual("", file_result.stdout)
        self.assertEqual("", file_result.stderr)
        self.assertEqual(inventory, json.loads(output.read_text(encoding="utf-8")))

    def test_validate_and_status_contract(self):
        inventory_path = self.base / "inventory.json"
        discover = self.run_cli(
            "discover",
            self.repository,
            "--repository",
            "synthetic-java-service",
            "--output",
            inventory_path,
        )
        self.assertEqual(0, discover.returncode)

        valid = self.run_cli("validate", inventory_path, "--source", self.repository)
        self.assertEqual(0, valid.returncode)
        self.assertEqual({"errors": [], "valid": True}, json.loads(valid.stdout))

        unchanged = self.run_cli(
            "status", self.repository, "--inventory", inventory_path
        )
        self.assertEqual(0, unchanged.returncode)
        self.assertFalse(json.loads(unchanged.stdout)["changed"])

        (self.repository / "README.md").write_text("changed\n", encoding="utf-8")
        git(self.repository, "add", "README.md")
        git(self.repository, "commit", "-q", "-m", "Change fixture")
        changed = self.run_cli("status", self.repository, "--inventory", inventory_path)
        self.assertEqual(0, changed.returncode)
        self.assertTrue(json.loads(changed.stdout)["changed"])

        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory["observations"][0]["value"] = {"tampered": True}
        inventory_path.write_text(
            json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        invalid = self.run_cli("validate", inventory_path)
        self.assertEqual(1, invalid.returncode)
        self.assertFalse(json.loads(invalid.stdout)["valid"])

    def test_operational_failure_uses_stderr(self):
        nested = self.repository / "src"
        result = self.run_cli("preflight", nested)
        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("independent Git repository root", result.stderr)

    def test_evidence_select_writes_stable_valid_bundle(self):
        secret = self.repository / ".env"
        secret.write_text("SYNTHETIC_SECRET=must-not-appear\n", encoding="utf-8")
        git(self.repository, "add", ".env", "--force")
        git(self.repository, "commit", "-q", "-m", "Add excluded evidence fixture")
        inventory_path = self.base / "inventory.json"
        self.assertEqual(0, self.run_cli(
            "discover", self.repository, "--repository", "synthetic-java-service",
            "--output", inventory_path,
        ).returncode)
        first_path = self.base / "nested" / "evidence.json"
        first = self.run_cli(
            "evidence", "select", inventory_path, "--source", self.repository,
            "--output", first_path,
        )
        self.assertEqual(0, first.returncode)
        self.assertEqual("", first.stdout)
        self.assertEqual("", first.stderr)
        bundle = json.loads(first_path.read_text(encoding="utf-8"))
        self.assertEqual([], validate_evidence_bundle(bundle))
        self.assertEqual("application", bundle["kind"])
        self.assertTrue(bundle["selectedEvidence"])
        self.assertIn("profile-dependent", {item["code"] for item in bundle["gaps"]})
        self.assertIn(
            {"path": ".env", "reason": "sensitive-file"}, bundle["excluded"]
        )
        self.assertNotIn("must-not-appear", first_path.read_text(encoding="utf-8"))

        second_path = self.base / "evidence-again.json"
        second = self.run_cli(
            "evidence", "select", inventory_path, "--source", self.repository,
            "--output", second_path,
        )
        self.assertEqual(0, second.returncode)
        self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

    def test_evidence_select_rejects_stale_and_dirty_sources(self):
        inventory_path = self.base / "inventory.json"
        self.assertEqual(0, self.run_cli(
            "discover", self.repository, "--repository", "synthetic-java-service",
            "--output", inventory_path,
        ).returncode)
        output = self.base / "evidence.json"

        tampered_path = self.base / "tampered-inventory.json"
        tampered = json.loads(inventory_path.read_text(encoding="utf-8"))
        tampered["observations"][0]["value"] = {"tampered": True}
        tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
        invalid = self.run_cli(
            "evidence", "select", tampered_path, "--source", self.repository,
            "--output", output,
        )
        self.assertEqual(1, invalid.returncode)
        self.assertEqual("", invalid.stdout)
        self.assertIn("Invalid inventory", invalid.stderr)
        self.assertFalse(output.exists())

        (self.repository / "README.md").write_text("dirty\n", encoding="utf-8")
        dirty = self.run_cli(
            "evidence", "select", inventory_path, "--source", self.repository,
            "--output", output,
        )
        self.assertEqual(1, dirty.returncode)
        self.assertEqual("", dirty.stdout)
        self.assertIn("clean working tree", dirty.stderr)
        self.assertFalse(output.exists())

        git(self.repository, "add", "README.md")
        git(self.repository, "commit", "-q", "-m", "Advance source")
        stale = self.run_cli(
            "evidence", "select", inventory_path, "--source", self.repository,
            "--output", output,
        )
        self.assertEqual(1, stale.returncode)
        self.assertEqual("", stale.stdout)
        self.assertIn("Inventory commit does not match", stale.stderr)
        self.assertFalse(output.exists())

    def test_evidence_selection_enforces_content_bounds(self):
        for index in range(18):
            path = self.repository / "notes-{:02d}.md".format(index)
            path.write_text("x" * (17 * 1024) + "\n", encoding="utf-8")
        git(self.repository, "add", ".")
        git(self.repository, "commit", "-q", "-m", "Add bounded evidence fixtures")
        inventory_path = self.base / "bounded-inventory.json"
        self.assertEqual(0, self.run_cli(
            "discover", self.repository, "--repository", "synthetic-java-service",
            "--output", inventory_path,
        ).returncode)
        output = self.base / "bounded-evidence.json"
        result = self.run_cli(
            "evidence", "select", inventory_path, "--source", self.repository,
            "--output", output,
        )
        self.assertEqual(0, result.returncode)
        bundle = json.loads(output.read_text(encoding="utf-8"))
        self.assertLessEqual(
            sum(len(item["content"].encode("utf-8")) for item in bundle["selectedEvidence"]),
            256 * 1024,
        )
        self.assertTrue(all(
            len(item["content"].encode("utf-8")) <= 16 * 1024
            for item in bundle["selectedEvidence"]
        ))
        self.assertIn("content-truncated", {item["code"] for item in bundle["gaps"]})
        self.assertIn(
            "selection-total-content-limit",
            {item["reason"] for item in bundle["excluded"]},
        )


if __name__ == "__main__":
    unittest.main()
