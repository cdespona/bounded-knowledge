"""End-to-end tests using synthetic Java and Kotlin repositories."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy


TOOLS = Path(__file__).resolve().parents[1]
PROJECT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from landscape_core.discovery import discover, preflight  # noqa: E402
from landscape_core.git_repository import commit_sha  # noqa: E402
from landscape_core.validation import validate_inventory  # noqa: E402


def git(path, *args):
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


class LandscapeTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.base = Path(self.temporary_directory.name)

    def make_repository(self, fixture):
        source = PROJECT / "examples" / fixture
        target = self.base / fixture
        shutil.copytree(str(source), str(target))
        git(target, "init", "-q")
        git(target, "config", "user.name", "Mercurio Test")
        git(target, "config", "user.email", "mercurio@example.invalid")
        git(target, "add", ".")
        git(target, "commit", "-q", "-m", "Synthetic fixture")
        return target

    def test_java_inventory_is_deterministic_and_valid(self):
        repository = self.make_repository("synthetic-java-service")
        first = discover(repository, "synthetic-java-service")
        second = discover(repository, "synthetic-java-service")

        self.assertEqual(
            json.dumps(first, sort_keys=True, separators=(",", ":")),
            json.dumps(second, sort_keys=True, separators=(",", ":")),
        )
        self.assertEqual([], validate_inventory(first, source=repository))
        categories = {item["value"]["category"] for item in first["observations"] if "category" in item["value"]}
        self.assertIn("maven", categories)
        self.assertIn("java-source", categories)

    def test_tampered_observation_fails_validation(self):
        repository = self.make_repository("synthetic-java-service")
        inventory = discover(repository, "synthetic-java-service")
        tampered = deepcopy(inventory)
        tampered["observations"][0]["value"] = {"tampered": True}

        errors = validate_inventory(tampered, source=repository)
        self.assertTrue(any("id does not match its content" in error for error in errors))

    def test_kotlin_gradle_manifest_is_recognized(self):
        repository = self.make_repository("synthetic-kotlin-service")
        inventory = discover(repository, "synthetic-kotlin-service")
        categories = {item["value"]["category"] for item in inventory["observations"] if "category" in item["value"]}
        self.assertIn("gradle-kotlin", categories)
        self.assertIn("gradle-kotlin-settings", categories)
        self.assertIn("kotlin-source", categories)

    def test_sensitive_files_are_excluded_without_being_read(self):
        repository = self.make_repository("synthetic-java-service")
        secret = repository / ".env"
        secret.write_text("SYNTHETIC_SECRET=do-not-read\n", encoding="utf-8")
        git(repository, "add", ".env", "--force")
        git(repository, "commit", "-q", "-m", "Add sensitive fixture")

        inventory = discover(repository, "synthetic-java-service")
        self.assertIn(
            {"path": ".env", "reason": "sensitive-file"},
            inventory["excluded"],
        )
        source_paths = {
            item.get("source", {}).get("path") for item in inventory["observations"]
        }
        self.assertNotIn(".env", source_paths)

    def test_dirty_repository_fails_preflight(self):
        repository = self.make_repository("synthetic-kotlin-service")
        (repository / "README.md").write_text("uncommitted\n", encoding="utf-8")
        result = preflight(repository)
        self.assertFalse(result["clean"])
        with self.assertRaisesRegex(ValueError, "clean working tree"):
            discover(repository, "synthetic-kotlin-service")

    def test_commit_change_is_detectable(self):
        repository = self.make_repository("synthetic-java-service")
        inventory = discover(repository, "synthetic-java-service")
        self.assertEqual(commit_sha(repository), inventory["commit"])

        readme = repository / "README.md"
        readme.write_text("synthetic change\n", encoding="utf-8")
        git(repository, "add", "README.md")
        git(repository, "commit", "-q", "-m", "Change fixture")
        self.assertNotEqual(commit_sha(repository), inventory["commit"])

    def test_source_repository_customizations_are_reported(self):
        repository = self.make_repository("synthetic-java-service")
        agent = repository / ".github" / "agents" / "source.agent.md"
        agent.parent.mkdir(parents=True)
        agent.write_text("Source-owned instructions\n", encoding="utf-8")
        git(repository, "add", str(agent.relative_to(repository)))
        git(repository, "commit", "-q", "-m", "Add source customization")

        result = preflight(repository)
        self.assertEqual(
            [".github/agents/source.agent.md"], result["copilotConfigurationFiles"]
        )


if __name__ == "__main__":
    unittest.main()
