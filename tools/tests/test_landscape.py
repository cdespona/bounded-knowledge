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
from landscape_core.evidence import select_evidence  # noqa: E402
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
        manifest_kinds = {
            item["kind"] for item in first["observations"]
            if item["detector"]["name"] == "maven-manifest"
        }
        self.assertEqual(
            {"build-project", "declared-dependency", "build-plugin", "build-module", "manifest-gap"},
            manifest_kinds,
        )
        dependency = next(
            item for item in first["observations"]
            if item["kind"] == "declared-dependency"
        )
        self.assertEqual("${spring.version}", dependency["value"]["version"])
        self.assertEqual("property-reference", dependency["value"]["declaration"])
        self.assertEqual("pom.xml", dependency["source"]["path"])
        self.assertRegex(dependency["source"]["lines"], r"^[1-9][0-9]*-[1-9][0-9]*$")

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
        parsed = [
            item for item in inventory["observations"]
            if item["detector"]["name"] == "gradle-manifest"
        ]
        kinds = {item["kind"] for item in parsed}
        self.assertEqual(
            {"build-project", "declared-dependency", "build-plugin", "build-module", "manifest-gap"},
            kinds,
        )
        property_dependency = next(
            item for item in parsed
            if item["kind"] == "declared-dependency"
            and item["value"].get("artifact") == "junit-jupiter"
        )
        self.assertEqual("$junitVersion", property_dependency["value"]["version"])
        self.assertEqual("property-reference", property_dependency["value"]["declaration"])
        gap_codes = {item["value"]["code"] for item in parsed if item["kind"] == "manifest-gap"}
        self.assertTrue({"dynamic-plugin", "dynamic-dependency", "unsupported-settings"} <= gap_codes)

    def test_malformed_maven_xml_is_a_visible_gap(self):
        repository = self.make_repository("synthetic-java-service")
        (repository / "pom.xml").write_text("<project><artifactId>broken", encoding="utf-8")
        git(repository, "add", "pom.xml")
        git(repository, "commit", "-q", "-m", "Break synthetic POM")

        inventory = discover(repository, "synthetic-java-service")
        self.assertEqual([], validate_inventory(inventory, source=repository))
        gaps = [item for item in inventory["observations"] if item["kind"] == "manifest-gap"]
        self.assertEqual(["unsupported-xml"], [item["value"]["code"] for item in gaps])

    def test_sensitive_files_are_excluded_without_being_read(self):
        repository = self.make_repository("synthetic-java-service")
        names = (
            ".env", ".env_migration", ".env_migration_example", ".envrc",
            "terraform.tfvars", "service.auto.tfvars", "service.tfvars.json",
            "service.tfplan",
        )
        for name in names:
            (repository / name).write_text("SYNTHETIC_SECRET=do-not-read\n", encoding="utf-8")
            git(repository, "add", name, "--force")
        git(repository, "commit", "-q", "-m", "Add sensitive fixture")

        inventory = discover(repository, "synthetic-java-service")
        bundle = select_evidence(inventory, repository)
        source_paths = {
            item.get("source", {}).get("path") for item in inventory["observations"]
        }
        selected_paths = {item["path"] for item in bundle["selectedEvidence"]}
        for name in names:
            self.assertIn({"path": name, "reason": "sensitive-file"}, inventory["excluded"])
            self.assertNotIn(name, source_paths)
            self.assertNotIn(name, selected_paths)

    def test_outbound_port_package_is_not_treated_as_build_output(self):
        repository = self.make_repository("synthetic-java-service")
        outbound = (
            repository
            / "src/main/java/com/example/mercurio/customer/port/out/CustomerPublisher.java"
        )
        outbound.parent.mkdir(parents=True)
        outbound.write_text("interface CustomerPublisher {}\n", encoding="utf-8")
        build_output = repository / "out/generated.txt"
        build_output.parent.mkdir()
        build_output.write_text("generated\n", encoding="utf-8")
        git(repository, "add", ".")
        git(repository, "commit", "-q", "-m", "Add outbound port and build output")

        inventory = discover(repository, "synthetic-java-service")
        self.assertEqual([], validate_inventory(inventory, source=repository))
        source_paths = {
            item.get("source", {}).get("path") for item in inventory["observations"]
        }
        self.assertIn(
            "src/main/java/com/example/mercurio/customer/port/out/CustomerPublisher.java",
            source_paths,
        )
        self.assertIn(
            {"path": "out", "reason": "excluded-directory"}, inventory["excluded"]
        )

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

    def test_evidence_selection_prioritizes_literal_contracts_and_reports_coverage(self):
        repository = self.make_repository("synthetic-java-service")
        for index in range(256):
            (repository / "a{:03d}.md".format(index)).write_text("ordinary file\n", encoding="utf-8")
        (repository / "z.openapi.json").write_text(
            '{"openapi":"3.1.0","info":{"title":"Synthetic","version":"1"},"paths":{"/items":{"get":{"operationId":"listItems","responses":{"200":{"description":"Synthetic"}}}}}}\n',
            encoding="utf-8",
        )
        git(repository, "add", ".")
        git(repository, "commit", "-q", "-m", "Add selection priority fixture")

        inventory = discover(repository, "synthetic-java-service")
        first = select_evidence(inventory, repository)
        second = select_evidence(inventory, repository)
        coverage = first["selectionCoverage"]

        self.assertEqual(first, second)
        self.assertIn("z.openapi.json", {item["path"] for item in first["selectedEvidence"]})
        self.assertEqual(256, coverage["selectedPaths"])
        self.assertEqual(
            coverage["selectedRanges"],
            sum(item["selectedRanges"] for item in coverage["byCategory"]),
        )
        self.assertGreater(coverage["omittedPaths"], 0)
        self.assertEqual(
            ["api-contract", "config-literal", "container-literal", "kafka-literal", "manifest", "repository-file"],
            [item["category"] for item in coverage["byCategory"]],
        )
        self.assertIn(
            "selection-file-limit",
            {item["reason"] for item in coverage["omittedByReason"]},
        )

    def test_source_owned_customizations_are_omitted_from_default_evidence(self):
        repository = self.make_repository("synthetic-java-service")
        customization = repository / ".github" / "copilot-instructions.md"
        customization.parent.mkdir(parents=True)
        customization.write_text("untrusted customization\n", encoding="utf-8")
        git(repository, "add", str(customization.relative_to(repository)))
        git(repository, "commit", "-q", "-m", "Add customization fixture")

        bundle = select_evidence(discover(repository, "synthetic-java-service"), repository)

        self.assertNotIn(
            ".github/copilot-instructions.md",
            {item["path"] for item in bundle["selectedEvidence"]},
        )
        self.assertIn(
            {"path": ".github/copilot-instructions.md", "reason": "source-owned-customization"},
            bundle["excluded"],
        )
        self.assertIn(
            "source-owned-customization",
            {item["reason"] for item in bundle["selectionCoverage"]["omittedByReason"]},
        )

    def test_semantic_selection_prioritizes_production_roles_and_records_omissions(self):
        repository = self.make_repository("synthetic-java-service")
        services = repository / "src/main/java/example/service"
        services.mkdir(parents=True)
        for index in range(260):
            (services / "Service{:03d}.java".format(index)).write_text("class Service{} {{}}\n".format(index), encoding="utf-8")
        entrypoint = repository / "src/main/java/example/stream/ZProductConsumer.java"
        entrypoint.parent.mkdir(parents=True)
        entrypoint.write_text("class ZProductConsumer {}\n", encoding="utf-8")
        module_port = repository / "module-a/src/main/kotlin/example/port/LoadProducts.kt"
        module_port.parent.mkdir(parents=True)
        module_port.write_text("interface LoadProducts\n", encoding="utf-8")
        schema = repository / "module-a/src/main/avro/Product.avsc"
        schema.parent.mkdir(parents=True)
        schema.write_text('{"type":"record","name":"Product","fields":[]}\n', encoding="utf-8")
        data = repository / "src/main/resources/product-data.json"
        data.parent.mkdir(parents=True, exist_ok=True)
        data.write_text("PRODUCT_DATA_SENTINEL\n", encoding="utf-8")
        (repository / "notes.md").write_text("DOC_SENTINEL\n", encoding="utf-8")
        customization = repository / ".github/copilot-instructions.md"
        customization.parent.mkdir(exist_ok=True)
        customization.write_text("CUSTOMIZATION_SENTINEL\n", encoding="utf-8")
        git(repository, "add", ".")
        git(repository, "commit", "-q", "-m", "Add semantic selection fixture")

        inventory = discover(repository, "synthetic-java-service")
        first = select_evidence(inventory, repository, purpose="semantic")
        self.assertEqual(first, select_evidence(inventory, repository, purpose="semantic"))
        self.assertEqual("semantic", first["selectionPurpose"])
        selected = {item["path"] for item in first["selectedEvidence"]}
        self.assertIn(entrypoint.relative_to(repository).as_posix(), selected)
        self.assertIn(module_port.relative_to(repository).as_posix(), selected)
        self.assertIn(schema.relative_to(repository).as_posix(), selected)
        self.assertEqual(256, first["selectionCoverage"]["selectedPaths"])
        self.assertNotIn(data.relative_to(repository).as_posix(), selected)
        self.assertNotIn("notes.md", selected)
        self.assertNotIn("PRODUCT_DATA_SENTINEL", json.dumps(first))
        self.assertNotIn("DOC_SENTINEL", json.dumps(first))
        self.assertNotIn("CUSTOMIZATION_SENTINEL", json.dumps(first))
        self.assertIn("semantic-scope", {item["reason"] for item in first["selectionCoverage"]["omittedByReason"]})
        self.assertIn("source-owned-customization", {item["reason"] for item in first["selectionCoverage"]["omittedByReason"]})
        self.assertIn("selection-file-limit", {item["reason"] for item in first["selectionCoverage"]["omittedByReason"]})


if __name__ == "__main__":
    unittest.main()
