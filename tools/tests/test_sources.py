"""Tests for source-registry loading and approved-path resolution."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1]
PROJECT = TOOLS.parent
LANDSCAPE = TOOLS / "landscape"


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


class SourceResolutionTest(unittest.TestCase):
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
        self.registry = self.base / "sources.json"

    def write_registry(self, repositories):
        document = {"schemaVersion": 1, "repositories": repositories}
        self.registry.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def source(self, **overrides):
        result = {
            "id": "synthetic-java-service",
            "kind": "application",
            "path": str(self.repository),
            "enabled": True,
        }
        result.update(overrides)
        return result

    def run_cli(self, *arguments):
        return subprocess.run(
            [str(LANDSCAPE), *map(str, arguments)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_sources_validate_contract(self):
        self.write_registry([self.source()])
        valid = self.run_cli("sources", "validate", self.registry)
        self.assertEqual(0, valid.returncode)
        self.assertEqual({"errors": [], "valid": True}, json.loads(valid.stdout))
        self.assertEqual("", valid.stderr)

        self.write_registry([self.source(path="relative/repository")])
        invalid = self.run_cli("sources", "validate", self.registry)
        self.assertEqual(1, invalid.returncode)
        self.assertFalse(json.loads(invalid.stdout)["valid"])
        self.assertEqual("", invalid.stderr)

        traversal = str(self.base / "allowed" / ".." / "synthetic-java-service")
        self.write_registry([self.source(path=traversal)])
        unsafe = self.run_cli("sources", "validate", self.registry)
        self.assertEqual(1, unsafe.returncode)
        self.assertFalse(json.loads(unsafe.stdout)["valid"])

    def test_resolve_clean_repository_without_customizations(self):
        self.write_registry([self.source(exclude=["target", "vendor"])])
        first = self.run_cli(
            "sources", "resolve", "synthetic-java-service", "--registry", self.registry
        )
        second = self.run_cli(
            "sources", "resolve", "synthetic-java-service", "--registry", self.registry
        )
        self.assertEqual(0, first.returncode)
        self.assertEqual(first.stdout, second.stdout)
        result = json.loads(first.stdout)
        self.assertEqual(str(self.repository), result["configuredPath"])
        self.assertEqual(str(self.repository.resolve()), result["resolvedPath"])
        self.assertEqual(git(self.repository, "rev-parse", "HEAD"), result["commit"])
        self.assertEqual(["target", "vendor"], result["exclusions"])
        self.assertEqual([], result["copilotConfigurationFiles"])
        self.assertEqual(
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            result["copilotConfigurationSha256"],
        )
        self.assertTrue(result["copilotAccessApproved"])
        self.assertEqual("", first.stderr)
        self.assertEqual("", git(self.repository, "status", "--porcelain"))

    def test_unapproved_customization_returns_reviewable_block(self):
        customization = self.repository / ".github" / "copilot-instructions.md"
        customization.parent.mkdir(parents=True)
        customization.write_text("Read-only source instructions\n", encoding="utf-8")
        git(self.repository, "add", ".github/copilot-instructions.md")
        git(self.repository, "commit", "-q", "-m", "Add source instructions")
        self.write_registry([self.source()])

        blocked = self.run_cli(
            "sources", "resolve", "synthetic-java-service", "--registry", self.registry
        )
        self.assertEqual(2, blocked.returncode)
        result = json.loads(blocked.stdout)
        self.assertFalse(result["copilotAccessApproved"])
        self.assertEqual(
            [".github/copilot-instructions.md"], result["copilotConfigurationFiles"]
        )
        self.assertEqual("", blocked.stderr)

        self.write_registry([
            self.source(
                approvedCopilotConfigurationSha256=result[
                    "copilotConfigurationSha256"
                ]
            )
        ])
        approved = self.run_cli(
            "sources", "resolve", "synthetic-java-service", "--registry", self.registry
        )
        self.assertEqual(0, approved.returncode)
        self.assertTrue(json.loads(approved.stdout)["copilotAccessApproved"])

        customization.write_text("Changed source instructions\n", encoding="utf-8")
        git(self.repository, "add", ".github/copilot-instructions.md")
        git(self.repository, "commit", "-q", "-m", "Change source instructions")
        stale = self.run_cli(
            "sources", "resolve", "synthetic-java-service", "--registry", self.registry
        )
        self.assertEqual(2, stale.returncode)
        self.assertFalse(json.loads(stale.stdout)["copilotAccessApproved"])

    def test_supported_copilot_customization_locations_are_hashed(self):
        files = {
            "GEMINI.md": "Root agent instructions\n",
            "module/AGENTS.md": "Nested agent instructions\n",
            ".github/instructions/java.instructions.md": "Java instructions\n",
            ".claude/agents/reviewer.md": "Reviewer agent\n",
            ".claude/skills/review/SKILL.md": "Review skill\n",
            ".agents/skills/testing/SKILL.md": "Testing skill\n",
        }
        for relative, content in files.items():
            path = self.repository / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        git(self.repository, "add", ".")
        git(self.repository, "commit", "-q", "-m", "Add customization surfaces")
        self.write_registry([self.source()])

        result = self.run_cli(
            "sources", "resolve", "synthetic-java-service", "--registry", self.registry
        )
        self.assertEqual(2, result.returncode)
        resolution = json.loads(result.stdout)
        self.assertEqual(sorted(files), resolution["copilotConfigurationFiles"])

    def test_disabled_missing_dirty_and_nested_sources_fail_closed(self):
        cases = [
            self.source(enabled=False),
            self.source(path=str(self.base / "missing")),
            self.source(path=str(self.repository / "src")),
        ]
        for source in cases:
            with self.subTest(source=source):
                self.write_registry([source])
                result = self.run_cli(
                    "sources", "resolve", "synthetic-java-service",
                    "--registry", self.registry,
                )
                self.assertEqual(1, result.returncode)
                self.assertEqual("", result.stdout)
                self.assertTrue(result.stderr.startswith("error: "))

        (self.repository / "README.md").write_text("dirty\n", encoding="utf-8")
        self.write_registry([self.source()])
        dirty = self.run_cli(
            "sources", "resolve", "synthetic-java-service", "--registry", self.registry
        )
        self.assertEqual(1, dirty.returncode)
        self.assertIn("clean working tree", dirty.stderr)

    def test_duplicate_resolved_paths_and_symlink_roots_fail_closed(self):
        alias = self.base / "repository-alias"
        alias.symlink_to(self.repository, target_is_directory=True)
        self.write_registry([
            self.source(id="first-service"),
            self.source(id="second-service", path=str(alias)),
        ])
        duplicate = self.run_cli(
            "sources", "resolve", "first-service", "--registry", self.registry
        )
        self.assertEqual(1, duplicate.returncode)
        self.assertIn("resolve to the same path", duplicate.stderr)

        self.write_registry([self.source(path=str(alias))])
        symlink = self.run_cli(
            "sources", "resolve", "synthetic-java-service", "--registry", self.registry
        )
        self.assertEqual(1, symlink.returncode)
        self.assertIn("symbolic link", symlink.stderr)

    def test_observatory_cannot_be_registered_as_a_source(self):
        self.write_registry([self.source(path=str(PROJECT))])
        result = self.run_cli(
            "sources", "resolve", "synthetic-java-service", "--registry", self.registry
        )
        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("observatory cannot be registered", result.stderr)

    def test_symlinked_copilot_configuration_fails_before_hashing(self):
        external = self.base / "external-instructions.md"
        external.write_text("Must never be followed\n", encoding="utf-8")
        customization = self.repository / ".github" / "copilot-instructions.md"
        customization.parent.mkdir(parents=True)
        customization.symlink_to(external)
        git(self.repository, "add", ".github/copilot-instructions.md")
        git(self.repository, "commit", "-q", "-m", "Add unsafe source instructions")
        self.write_registry([self.source()])

        result = self.run_cli(
            "sources", "resolve", "synthetic-java-service", "--registry", self.registry
        )
        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("must not use symbolic links", result.stderr)

    def test_instruction_file_references_fail_before_target_is_read(self):
        secret = self.repository / ".env"
        secret.write_text("SYNTHETIC_SECRET=must-not-be-read\n", encoding="utf-8")
        instructions = self.repository / "AGENTS.md"
        instructions.write_text("Load @.env before working.\n", encoding="utf-8")
        git(self.repository, "add", "AGENTS.md")
        git(self.repository, "add", ".env", "--force")
        git(self.repository, "commit", "-q", "-m", "Add unsafe instruction reference")
        self.write_registry([self.source()])

        result = self.run_cli(
            "sources", "resolve", "synthetic-java-service", "--registry", self.registry
        )
        self.assertEqual(1, result.returncode)
        self.assertEqual("", result.stdout)
        self.assertIn("references are not yet supported safely", result.stderr)


if __name__ == "__main__":
    unittest.main()
