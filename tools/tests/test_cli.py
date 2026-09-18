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
        self.assertIn(
            "{preflight,discover,validate,status,sources,evidence,candidate,catalog,topology}",
            result.stdout,
        )
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

    def test_candidate_validate_contract(self):
        fixtures = PROJECT / "examples" / "contracts"
        valid = self.run_cli(
            "candidate", "validate", fixtures / "candidate-envelope.valid.json",
            "--evidence", fixtures / "evidence-bundle.valid.json",
        )
        self.assertEqual(0, valid.returncode)
        self.assertEqual({"errors": [], "valid": True}, json.loads(valid.stdout))
        self.assertEqual("", valid.stderr)

        invalid = self.run_cli(
            "candidate", "validate", fixtures / "candidate-envelope.invalid.json",
            "--evidence", fixtures / "evidence-bundle.valid.json",
        )
        self.assertEqual(1, invalid.returncode)
        self.assertFalse(json.loads(invalid.stdout)["valid"])
        self.assertEqual("", invalid.stderr)

        malformed_path = self.base / "malformed.json"
        malformed_path.write_text("not JSON\n", encoding="utf-8")
        malformed = self.run_cli(
            "candidate", "validate", malformed_path,
            "--evidence", fixtures / "evidence-bundle.valid.json",
        )
        self.assertEqual(1, malformed.returncode)
        self.assertEqual("", malformed.stdout)
        self.assertTrue(malformed.stderr.startswith("error: "))

    def test_candidate_extract_contract(self):
        fixtures = PROJECT / "examples" / "contracts"
        candidate = (fixtures / "candidate-envelope.valid.json").read_text(
            encoding="utf-8"
        )
        response = self.base / "candidate-response.txt"
        response.write_text("Preparing bounded result.\n\n" + candidate, encoding="utf-8")
        output = self.base / "nested" / "candidate.json"
        extracted = self.run_cli("candidate", "extract", response, "--output", output)
        self.assertEqual(0, extracted.returncode)
        self.assertEqual("", extracted.stdout)
        self.assertEqual("", extracted.stderr)
        self.assertEqual(json.loads(candidate), json.loads(output.read_text(encoding="utf-8")))

        multiple = self.base / "multiple-response.txt"
        multiple.write_text(candidate + candidate, encoding="utf-8")
        rejected = self.run_cli(
            "candidate", "extract", multiple, "--output", self.base / "rejected.json"
        )
        self.assertEqual(1, rejected.returncode)
        self.assertEqual("", rejected.stdout)
        self.assertTrue(rejected.stderr.startswith("error: "))
        self.assertFalse((self.base / "rejected.json").exists())

        for name, content in (
            ("empty", "No candidate object was produced.\n"),
            ("array", "[{}]".format(candidate)),
        ):
            invalid_response = self.base / "{}-response.txt".format(name)
            invalid_output = self.base / "{}-candidate.json".format(name)
            invalid_response.write_text(content, encoding="utf-8")
            invalid = self.run_cli(
                "candidate", "extract", invalid_response, "--output", invalid_output
            )
            self.assertEqual(1, invalid.returncode, name)
            self.assertEqual("", invalid.stdout, name)
            self.assertTrue(invalid.stderr.startswith("error: "), name)
            self.assertFalse(invalid_output.exists(), name)

    def test_catalog_validate_contract(self):
        fixtures = PROJECT / "examples" / "contracts"
        catalog = fixtures / "landscape-catalog.valid.json"
        valid = self.run_cli("catalog", "validate", catalog)
        repeated = self.run_cli("catalog", "validate", catalog)
        self.assertEqual(0, valid.returncode)
        self.assertEqual(valid.stdout, repeated.stdout)
        self.assertEqual({"errors": [], "valid": True}, json.loads(valid.stdout))
        self.assertEqual("", valid.stderr)

        invalid = self.run_cli(
            "catalog", "validate", fixtures / "landscape-catalog.invalid.json"
        )
        self.assertEqual(1, invalid.returncode)
        self.assertFalse(json.loads(invalid.stdout)["valid"])
        self.assertEqual("", invalid.stderr)

        malformed_path = self.base / "malformed-catalog.json"
        malformed_path.write_text("not JSON\n", encoding="utf-8")
        malformed = self.run_cli("catalog", "validate", malformed_path)
        self.assertEqual(1, malformed.returncode)
        self.assertEqual("", malformed.stdout)
        self.assertTrue(malformed.stderr.startswith("error: "))

    def test_topology_validate_contract_is_stable_and_read_only(self):
        fixtures = PROJECT / "examples" / "contracts"
        topology_path = fixtures / "source-topology.valid.json"
        topology = json.loads(topology_path.read_text(encoding="utf-8"))
        sources_path = self.base / "sources.json"
        sources = {
            "schemaVersion": 1,
            "repositories": [
                {
                    "enabled": True,
                    "id": item["id"],
                    "kind": item["kind"],
                    "path": str(self.base / item["id"]),
                }
                for item in topology["repositories"]
            ],
        }
        sources_path.write_text(
            json.dumps(sources, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        before = sources_path.read_bytes()
        arguments = (
            "topology", "validate", topology_path,
            "--sources", sources_path,
            "--catalog", fixtures / "landscape-catalog.valid.json",
        )
        valid = self.run_cli(*arguments)
        repeated = self.run_cli(*arguments)
        self.assertEqual(0, valid.returncode)
        self.assertEqual(valid.stdout, repeated.stdout)
        self.assertEqual({"errors": [], "valid": True}, json.loads(valid.stdout))
        self.assertEqual("", valid.stderr)
        self.assertEqual(before, sources_path.read_bytes())

        invalid = self.run_cli(
            "topology", "validate", fixtures / "source-topology.invalid.json",
            "--sources", sources_path,
            "--catalog", fixtures / "landscape-catalog.valid.json",
        )
        self.assertEqual(1, invalid.returncode)
        self.assertFalse(json.loads(invalid.stdout)["valid"])
        self.assertEqual("", invalid.stderr)

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
