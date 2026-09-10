"""Focused tests for deterministic OpenAPI and AsyncAPI discovery."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1]
PROJECT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from landscape_core.discovery import discover  # noqa: E402
from landscape_core.evidence import select_evidence  # noqa: E402
from landscape_core.safety import MAX_FILE_BYTES  # noqa: E402
from landscape_core.validation import validate_inventory  # noqa: E402


def git(path, *args):
    subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class ApiContractDetectorTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.base = Path(self.temporary_directory.name)

    def make_repository(self, fixture):
        target = self.base / fixture
        shutil.copytree(str(PROJECT / "examples" / fixture), str(target))
        git(target, "init", "-q")
        git(target, "config", "user.name", "Mercurio Test")
        git(target, "config", "user.email", "mercurio@example.invalid")
        git(target, "add", ".")
        git(target, "commit", "-q", "-m", "Synthetic fixture")
        return target

    def api_observations(self, inventory):
        return [
            item for item in inventory["observations"]
            if item["detector"]["name"] == "api-contract"
        ]

    def test_openapi_json_is_literal_stable_and_valid(self):
        repository = self.make_repository("synthetic-java-service")
        first = discover(repository, "synthetic-java-service")
        second = discover(repository, "synthetic-java-service")
        self.assertEqual(first, second)
        self.assertEqual([], validate_inventory(first, source=repository))
        observations = self.api_observations(first)
        document = next(item for item in observations if item["kind"] == "api-document")
        operation = next(item for item in observations if item["kind"] == "api-operation")
        self.assertEqual("3.1.0", document["value"]["specificationVersion"])
        self.assertEqual("/customers", operation["value"]["target"])
        self.assertEqual("get", operation["value"]["action"])
        self.assertEqual("listCustomers", operation["value"]["operationId"])
        self.assertEqual("src/main/resources/openapi.json", operation["source"]["path"])
        self.assertEqual("8-10", operation["source"]["lines"])

    def test_asyncapi3_and_yaml_gap_remain_distinct(self):
        repository = self.make_repository("synthetic-kotlin-service")
        inventory = discover(repository, "synthetic-kotlin-service")
        self.assertEqual([], validate_inventory(inventory, source=repository))
        observations = self.api_observations(inventory)
        operation = next(item for item in observations if item["kind"] == "api-operation")
        self.assertEqual("publishCustomerUpdated", operation["value"]["operationKey"])
        self.assertEqual("send", operation["value"]["action"])
        self.assertEqual("#/channels/customerUpdated", operation["value"]["target"])
        gap = next(item for item in observations if item["kind"] == "api-gap")
        self.assertEqual("unsupported-yaml", gap["value"]["code"])
        self.assertEqual("1", gap["source"]["lines"])

    def test_asyncapi2_preserves_channel_operation_vocabulary(self):
        repository = self.make_repository("synthetic-java-service")
        contract = repository / "contracts" / "legacy.asyncapi.json"
        contract.parent.mkdir()
        contract.write_text(json.dumps({
            "asyncapi": "2.6.0",
            "info": {"title": "Legacy", "version": "1"},
            "channels": {
                "customer.created": {
                    "publish": {"operationId": "publishCustomerCreated"}
                }
            },
        }, indent=2) + "\n", encoding="utf-8")
        git(repository, "add", ".")
        git(repository, "commit", "-q", "-m", "Add AsyncAPI 2 fixture")
        observations = self.api_observations(discover(repository, "synthetic-java-service"))
        operation = next(
            item for item in observations
            if item["kind"] == "api-operation"
            and item["value"].get("operationId") == "publishCustomerCreated"
        )
        self.assertEqual("publish", operation["value"]["action"])
        self.assertEqual("customer.created", operation["value"]["target"])
        self.assertNotIn("operationKey", operation["value"])

    def test_malformed_duplicate_and_unsupported_json_are_visible(self):
        repository = self.make_repository("synthetic-java-service")
        resources = repository / "src" / "main" / "resources"
        (resources / "broken.openapi.json").write_text(
            '{\n  "openapi": "3.1.0",\n  broken\n}\n', encoding="utf-8"
        )
        (resources / "duplicate.openapi.json").write_text(
            '{"openapi":"3.1.0","openapi":"3.2.0"}\n', encoding="utf-8"
        )
        (resources / "future.asyncapi.json").write_text(
            '{"asyncapi":"4.0.0","info":{"title":"Future","version":"1"}}\n',
            encoding="utf-8",
        )
        (resources / "missing.openapi.json").write_text("{}\n", encoding="utf-8")
        (resources / "conflict.asyncapi.json").write_text(
            '{\n  "openapi":"3.1.0",\n  "asyncapi":"3.1.0"\n}\n', encoding="utf-8"
        )
        git(repository, "add", ".")
        git(repository, "commit", "-q", "-m", "Add invalid API fixtures")
        observations = self.api_observations(discover(repository, "synthetic-java-service"))
        codes = {item["value"]["code"] for item in observations if item["kind"] == "api-gap"}
        self.assertTrue({
            "conflicting-specification-markers",
            "duplicate-json-key",
            "malformed-json",
            "missing-specification-marker",
            "unsupported-version",
        } <= codes)
        gaps = {
            (item["source"]["path"], item["value"]["code"]): item["source"]["lines"]
            for item in observations if item["kind"] == "api-gap"
        }
        self.assertEqual(
            "3",
            gaps[("src/main/resources/broken.openapi.json", "malformed-json")],
        )
        self.assertEqual(
            "2-3",
            gaps[(
                "src/main/resources/conflict.asyncapi.json",
                "conflicting-specification-markers",
            )],
        )

    def test_supported_version_families_are_routed_explicitly(self):
        repository = self.make_repository("synthetic-java-service")
        contracts = repository / "contracts"
        contracts.mkdir()
        documents = {
            "v30.openapi.json": {
                "openapi": "3.0.4", "info": {"title": "OAS 3.0", "version": "1"},
                "paths": {"/v30": {"get": {}}},
            },
            "v32.openapi.json": {
                "openapi": "3.2.1", "info": {"title": "OAS 3.2", "version": "1"},
                "paths": {"/search": {"query": {}}},
            },
            "v30.asyncapi.json": {
                "asyncapi": "3.0.0", "info": {"title": "AAS 3.0", "version": "1"},
                "operations": {
                    "receiveCustomer": {
                        "action": "receive",
                        "channel": {"$ref": "#/channels/customer"},
                    }
                },
            },
        }
        for name, document in documents.items():
            (contracts / name).write_text(
                json.dumps(document, indent=2) + "\n", encoding="utf-8"
            )
        git(repository, "add", ".")
        git(repository, "commit", "-q", "-m", "Add supported API versions")
        observations = self.api_observations(discover(repository, "synthetic-java-service"))
        versions = {
            item["value"]["specificationVersion"]
            for item in observations if item["kind"] == "api-document"
        }
        self.assertTrue({"3.0.4", "3.1.0", "3.2.1", "3.0.0"} <= versions)
        operations = {
            (item["value"]["action"], item["value"]["target"])
            for item in observations if item["kind"] == "api-operation"
        }
        self.assertTrue({
            ("get", "/v30"),
            ("query", "/search"),
            ("receive", "#/channels/customer"),
        } <= operations)

    def test_non_candidate_json_is_not_parsed(self):
        repository = self.make_repository("synthetic-java-service")
        irrelevant = repository / "src" / "main" / "resources" / "application.json"
        irrelevant.write_bytes(b"\xff\xfe")
        git(repository, "add", ".")
        git(repository, "commit", "-q", "-m", "Add irrelevant binary JSON")
        inventory = discover(repository, "synthetic-java-service")
        self.assertEqual([], validate_inventory(inventory, source=repository))
        api_paths = {
            item["source"]["path"] for item in self.api_observations(inventory)
        }
        self.assertNotIn("src/main/resources/application.json", api_paths)

    def test_structural_lines_include_every_derived_literal(self):
        repository = self.make_repository("synthetic-java-service")
        path = repository / "src" / "main" / "resources" / "collision.openapi.json"
        path.write_text(
            '{\n'
            '  "metadata": {"/collision": {"get": "not an operation"}},\n'
            '  "openapi": "3.2.0",\n'
            '  "info": {"title": "Collision", "version": "1"},\n'
            '  "paths": {\n'
            '    "/collision": {\n'
            '      "get": {\n'
            '        "operationId": "realOperation"\n'
            '      }\n'
            '    }\n'
            '  }\n'
            '}\n',
            encoding="utf-8",
        )
        git(repository, "add", ".")
        git(repository, "commit", "-q", "-m", "Add locator collision")
        operation = next(
            item for item in self.api_observations(
                discover(repository, "synthetic-java-service")
            )
            if item["kind"] == "api-operation"
            and item["value"].get("operationId") == "realOperation"
        )
        self.assertEqual("6-8", operation["source"]["lines"])

    def test_cr_delimited_json_uses_the_same_line_model_as_evidence_selection(self):
        repository = self.make_repository("synthetic-java-service")
        path = repository / "src" / "main" / "resources" / "cr.openapi.json"
        path.write_bytes(
            b'{\r  "openapi": "3.1.0",\r  "info": {"title": "CR", "version": "1"},'
            b'\r  "paths": {\r    "/cr": {\r      "get": {\r'
            b'        "operationId": "readCr"\r      }\r    }\r  }\r}\r'
        )
        git(repository, "add", ".")
        git(repository, "commit", "-q", "-m", "Add CR-delimited API")
        operation = next(
            item for item in self.api_observations(
                discover(repository, "synthetic-java-service")
            )
            if item["kind"] == "api-operation"
            and item["value"].get("operationId") == "readCr"
        )
        self.assertEqual("5-7", operation["source"]["lines"])

    def test_parser_resource_limit_fails_closed(self):
        repository = self.make_repository("synthetic-java-service")
        path = repository / "src" / "main" / "resources" / "deep.openapi.json"
        path.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
        git(repository, "add", ".")
        git(repository, "commit", "-q", "-m", "Add deeply nested JSON")
        codes = {
            item["value"]["code"]
            for item in self.api_observations(discover(repository, "synthetic-java-service"))
            if item["kind"] == "api-gap"
        }
        self.assertIn("parser-resource-limit", codes)

    def test_unsupported_operation_key_is_visible_and_independent_work_continues(self):
        repository = self.make_repository("synthetic-java-service")
        path = repository / "src" / "main" / "resources" / "mixed.openapi.json"
        path.write_text(json.dumps({
            "openapi": "3.1.0",
            "info": {"title": "Mixed", "version": "1"},
            "paths": {
                "/bad": {"GET": {}},
                "/good": {"post": {"operationId": "createGood"}},
            },
        }, indent=2) + "\n", encoding="utf-8")
        git(repository, "add", ".")
        git(repository, "commit", "-q", "-m", "Add mixed operations")
        observations = self.api_observations(discover(repository, "synthetic-java-service"))
        self.assertTrue(any(
            item["kind"] == "api-gap"
            and item["value"]["code"] == "unsupported-operation-key"
            for item in observations
        ))
        self.assertTrue(any(
            item["kind"] == "api-operation"
            and item["value"].get("operationId") == "createGood"
            for item in observations
        ))

    def test_candidate_safety_and_filename_boundary(self):
        repository = self.make_repository("synthetic-java-service")
        resources = repository / "src" / "main" / "resources"
        (resources / "CASE.OPENAPI.JSON").write_text(
            '{"openapi":"3.0.4","info":{"title":"Case","version":"1"},"paths":{}}\n',
            encoding="utf-8",
        )
        (resources / "binary.asyncapi.json").write_bytes(b"\xff\xfe")
        (resources / "secret.openapi.json").write_text("{}\n", encoding="utf-8")
        (resources / "large.openapi.json").write_bytes(b" " * (MAX_FILE_BYTES + 1))
        (resources / "link.asyncapi.json").symlink_to("asyncapi.json")
        git(repository, "add", ".")
        git(repository, "commit", "-q", "-m", "Add API safety fixtures")
        inventory = discover(repository, "synthetic-java-service")
        self.assertEqual([], validate_inventory(inventory, source=repository))
        api_paths = {
            item["source"]["path"] for item in self.api_observations(inventory)
        }
        self.assertIn("src/main/resources/CASE.OPENAPI.JSON", api_paths)
        self.assertNotIn("src/main/resources/secret.openapi.json", api_paths)
        self.assertNotIn("src/main/resources/link.asyncapi.json", api_paths)
        self.assertNotIn("src/main/resources/large.openapi.json", api_paths)
        self.assertIn(
            {
                "path": "src/main/resources/binary.asyncapi.json",
                "reason": "unsupported-or-unreadable-content",
            },
            inventory["excluded"],
        )
        self.assertIn(
            {
                "path": "src/main/resources/large.openapi.json",
                "reason": "file-too-large",
            },
            inventory["excluded"],
        )

    def test_api_gap_flows_to_evidence_gaps(self):
        repository = self.make_repository("synthetic-kotlin-service")
        inventory = discover(repository, "synthetic-kotlin-service")
        bundle = select_evidence(inventory, repository)
        api_gap_ids = {
            item["id"] for item in self.api_observations(inventory)
            if item["kind"] == "api-gap"
        }
        self.assertTrue(api_gap_ids)
        self.assertTrue(api_gap_ids <= {item["observationId"] for item in bundle["gaps"]})
        selected_ids = {
            item_id for entry in bundle["selectedEvidence"]
            for item_id in entry["observationIds"]
        }
        self.assertTrue(api_gap_ids.isdisjoint(selected_ids))


if __name__ == "__main__":
    unittest.main()
