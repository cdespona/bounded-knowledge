"""Focused acceptance coverage for bounded Terraform selection discovery."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
from landscape_core.discovery import discover  # noqa: E402
from landscape_core.evidence import select_evidence  # noqa: E402
from landscape_core.candidates import validate_candidate  # noqa: E402
from landscape_core.validation import validate_inventory  # noqa: E402


class TerraformInventoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.app = self.root / "infra/app"; self.app.mkdir(parents=True)
        self.topology = {"schemaVersion": 1, "repositories": [{"id": "tf", "kind": "terraform"}], "sourceSelections": [{"id": "app", "repositoryId": "tf", "subpath": "infra/app", "kind": "terraform"}], "bindings": []}

    def commit(self):
        for command in (("init", "-q"), ("config", "user.email", "test@example.invalid"), ("config", "user.name", "Test"), ("add", "."), ("commit", "-qm", "fixture")):
            subprocess.run(["git", "-C", str(self.root), *command], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def inventory(self): return discover(self.root, "tf", self.topology, "app")

    def status(self):
        return subprocess.run(["git", "-C", str(self.root), "status", "--porcelain"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout

    def test_declared_composition_references_gaps_and_exact_lines(self):
        (self.app / "main.tf").write_text('resource "aws_instance" "api" { ami = var.private_ami }\nmodule "payments" { source = "git::https://example.invalid/modules/payments" token = var.private_token }\nresource "aws_instance" "safe" {\n  ami = var.private_ami\n}\nmodule "network" {\n  source = "../network"\n}\nlocals { name = module.network.name }\n', encoding="utf-8")
        (self.app / "semicolon-example.tf").write_text('module "payments" { source = "git::https://example.invalid/modules/payments"; token = var.private_token }\n', encoding="utf-8")
        (self.app / "terragrunt.hcl").write_text('terraform { source = "../stack" }\ninclude "root" { path = find_in_parent_folders() }\ndependency "db" { config_path = "../db" }\ninputs = { password = "do-not-select" }\n', encoding="utf-8")
        (self.app / "legacy.tf.json").write_text('{not read}', encoding="utf-8")
        (self.app / "secret.tfvars").write_text('token="do-not-read"', encoding="utf-8")
        self.commit(); first, second = self.inventory(), self.inventory()
        self.assertEqual("", self.status(), "the parser must not write inside the analyzed repository")
        self.assertEqual(first, second); self.assertEqual([], validate_inventory(first, source=self.root))
        values = [item["value"] for item in first["observations"]]
        self.assertIn("aws_instance", [value.get("type") for value in values])
        self.assertIn("../network", [value.get("source") for value in values])
        self.assertIn("var.private_ami", [value.get("reference") for value in values])
        self.assertIn("unresolved-terragrunt-input", [value.get("code") for value in values])
        self.assertNotIn("do-not-read", str(first))
        bundle = select_evidence(first, self.root)
        content = "\n".join(item["content"] for item in bundle["selectedEvidence"])
        self.assertIn('resource "aws_instance" "safe" {', content)
        self.assertIn('source = "../network"', content)
        for private in ("private_ami", "private_token", "do-not-select"):
            self.assertNotIn(private, content)
        withheld = {item["observationId"] for item in bundle["gaps"] if item["code"] == "sensitive-value-withheld"}
        self.assertTrue(withheld)
        withheld_item = next(item for item in first["observations"] if item["id"] in withheld)
        candidate = {"schemaVersion": 1, "repository": "tf", "kind": "terraform", "analyzedCommit": first["commit"], "evidenceBundleId": bundle["id"], "proposedProfile": {"schemaVersion": 1, "repository": "tf", "kind": "terraform", "analyzedCommit": first["commit"], "openQuestions": [], "claims": [{"id": "withheld-source", "statement": "Must be rejected.", "status": "confirmed", "confidence": "low", "analyzedAt": "2026-01-01T00:00:00Z", "counterevidence": [], "evidence": [{"repository": "tf", "commit": first["commit"], "path": withheld_item["source"]["path"], "lines": withheld_item["source"]["lines"], "observationId": withheld_item["id"]}]}]}}
        self.assertTrue(validate_candidate(candidate, bundle))
        outside = json.loads(json.dumps(first))
        next(item for item in outside["observations"] if item["kind"].startswith("terraform-"))["source"]["path"] = "outside.tf"
        self.assertTrue(any("outside sourceSelection" in error for error in validate_inventory(outside)))

    def test_fatal_parse_and_safety_boundaries_do_not_leak(self):
        (self.app / "bad.tf").write_text('resource { secret = "no"', encoding="utf-8")
        (self.app / "oversized.tf").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        (self.app / ".terraform").mkdir(); (self.app / ".terraform/cache.tf").write_text('secret', encoding="utf-8")
        self.commit(); inventory = self.inventory()
        self.assertIn("malformed-hcl", [x["value"].get("code") for x in inventory["observations"]])
        self.assertNotIn("secret", str(inventory))
        self.assertIn({"path": "infra/app/oversized.tf", "reason": "file-too-large"}, inventory["excluded"])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_symlink_is_excluded_without_following(self):
        outside = self.root / "outside.tf"; outside.write_text('resource "x" "secret" { }', encoding="utf-8")
        (self.app / "linked.tf").symlink_to(outside); self.commit(); inventory = self.inventory()
        self.assertIn({"path": "infra/app/linked.tf", "reason": "symbolic-link"}, inventory["excluded"])


if __name__ == "__main__": unittest.main()
