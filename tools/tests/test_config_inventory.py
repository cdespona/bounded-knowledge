"""Focused tests for the application configuration inventory."""

import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from landscape_core.config_files import classify_resource, mask_config_text, parse_config  # noqa: E402
from landscape_core.discovery import discover  # noqa: E402
from landscape_core.evidence import select_evidence  # noqa: E402
from landscape_core.validation import validate_inventory  # noqa: E402


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


QUARKUS = """# generic values
sqs.events.url=http://localhost:4566/000000000000/local-events
%dev.sqs.events.url=https://sqs.eu-west-1.amazonaws.com/123456789012/dev-events
%pro.sqs.events.url=https://sqs.eu-west-1.amazonaws.com/123456789012/pro-events
%dev,test.other=skipped
db.password=hunter2
db.url=${DB_URL}
kafka.topic.merged = merged-products
long.value=one \\
  two
"""

SPRING_YAML = """spring:
  datasource:
    url: jdbc:oracle:thin:@//db.example.invalid:1521/SVC   # trailing comment
    password: s3cret
  kafka:
    bootstrap-servers: broker:9092
    topics:
      - first
      - second
notes: |
  free text
  more text
after: 1
"""


class ConfigParsingTest(unittest.TestCase):
    def entries(self, text, name):
        entries, gaps = parse_config(text, name)
        return {(item["key"], item["profile"]): item["value"] for item in entries}, gaps

    def test_properties_profiles_prefixes_and_gaps(self):
        entries, gaps = self.entries(QUARKUS, "src/main/resources/application.properties")
        self.assertEqual(entries[("sqs.events.url", None)], "http://localhost:4566/000000000000/local-events")
        self.assertIn("pro-events", entries[("sqs.events.url", "pro")])
        self.assertEqual(entries[("kafka.topic.merged", None)], "merged-products")
        self.assertNotIn(("other", "dev,test"), entries)
        self.assertEqual(sorted((item["line"], item["code"]) for item in gaps),
                         [(5, "unsupported-profile-expression"), (9, "unsupported-config-syntax")])

    def test_yaml_flattening_lists_and_unsupported_constructs(self):
        entries, gaps = self.entries(SPRING_YAML, "src/main/resources/application.yml")
        self.assertEqual(entries[("spring.datasource.url", None)], "jdbc:oracle:thin:@//db.example.invalid:1521/SVC")
        self.assertEqual(entries[("spring.kafka.topics[1]", None)], "second")
        self.assertEqual(entries[("after", None)], "1")
        self.assertNotIn(("notes", None), entries)
        self.assertEqual([item["code"] for item in gaps], ["unsupported-yaml-construct"])

    def test_logging_keys_are_not_classified_as_resources(self):
        entries, _ = parse_config('quarkus.log.category."org.apache.kafka".level=WARN\n', "src/main/resources/application.properties")
        self.assertIsNone(classify_resource(entries[0]["key"], entries[0]["value"]))

    def test_masking_withholds_sensitive_values_and_account_ids_line_for_line(self):
        masked = mask_config_text(QUARKUS, "src/main/resources/application.properties")
        self.assertNotIn("hunter2", masked)
        self.assertNotIn("123456789012", masked)
        self.assertIn("db.url=${DB_URL}", masked)
        self.assertEqual(len(masked.split("\n")), len(QUARKUS.split("\n")))


class ConfigInventoryTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = Path(directory.name) / "repo"
        resources = self.repository / "src" / "main" / "resources"
        (resources / "database").mkdir(parents=True)
        (resources / "application.properties").write_text(QUARKUS, encoding="utf-8")
        (resources / "application-pro.yml").write_text(SPRING_YAML, encoding="utf-8")
        (resources / "database" / "jdbc-pro.properties").write_text("url=jdbc:oracle:thin:@//h:1521/S\n", encoding="utf-8")
        test_resources = self.repository / "src" / "test" / "resources"
        test_resources.mkdir(parents=True)
        (test_resources / "application.properties").write_text("db.url=jdbc:h2:mem:test\n", encoding="utf-8")
        git(self.repository, "init", "-q")
        git(self.repository, "config", "user.name", "Test")
        git(self.repository, "config", "user.email", "test@example.invalid")
        git(self.repository, "add", ".")
        git(self.repository, "commit", "-q", "-m", "Fixture")
        self.inventory = discover(self.repository, "synthetic-config")

    def config(self):
        return [item for item in self.inventory["observations"] if item["detector"]["name"] == "config-properties"]

    def test_observations_cover_main_resources_only_with_profiles_and_resources(self):
        observations = self.config()
        self.assertEqual({item["source"]["path"] for item in observations},
                         {"src/main/resources/application.properties", "src/main/resources/application-pro.yml",
                          "src/main/resources/database/jdbc-pro.properties"})
        by_key = {(item["source"]["path"], item["value"]["key"], item["value"]["profile"]): item["value"]
                  for item in observations if item["kind"] == "config-property"}
        properties = "src/main/resources/application.properties"
        self.assertEqual(by_key[(properties, "sqs.events.url", "pro")]["resource"], "sqs")
        self.assertEqual(by_key[(properties, "sqs.events.url", "pro")]["value"],
                         "https://sqs.eu-west-1.amazonaws.com/<aws-account>/pro-events")
        self.assertEqual(by_key[(properties, "db.password", None)]["disposition"], "withheld")
        self.assertIsNone(by_key[(properties, "db.password", None)]["value"])
        self.assertEqual(by_key[(properties, "db.url", None)]["disposition"], "placeholder")
        self.assertEqual(by_key[(properties, "kafka.topic.merged", None)]["resource"], "kafka-topic")
        jdbc = by_key[("src/main/resources/database/jdbc-pro.properties", "url", "pro")]
        self.assertEqual(jdbc["resource"], "jdbc")
        self.assertEqual(by_key[("src/main/resources/application-pro.yml", "spring.kafka.bootstrap-servers", "pro")]["resource"], "kafka")

    def test_inventory_is_valid_stable_and_never_stores_secrets(self):
        self.assertEqual(validate_inventory(self.inventory, source=self.repository), [])
        self.assertEqual(self.inventory, discover(self.repository, "synthetic-config"))
        text = repr(self.inventory)
        for secret in ("hunter2", "s3cret", "123456789012"):
            self.assertNotIn(secret, text)

    def test_validation_rejects_withheld_values_that_carry_text(self):
        inventory = copy.deepcopy(self.inventory)
        item = next(item for item in inventory["observations"]
                    if item["kind"] == "config-property" and item["value"]["disposition"] == "withheld")
        item["value"]["value"] = "leaked"
        errors = validate_inventory(inventory, source=self.repository)
        self.assertTrue(any("exactly when the disposition is withheld" in error for error in errors), errors)

    def test_semantic_evidence_selects_masked_configuration(self):
        bundle = select_evidence(self.inventory, self.repository, purpose="semantic")
        selected = {item["path"]: item for item in bundle["selectedEvidence"]}
        content = selected["src/main/resources/application.properties"]["content"]
        self.assertIn("%pro.sqs.events.url=https://sqs.eu-west-1.amazonaws.com/<aws-account>/pro-events", content)
        self.assertIn("db.password=<withheld>", content)
        self.assertNotIn("hunter2", content)
        self.assertNotIn("src/test/resources/application.properties", selected)
        self.assertIn("src/main/resources/application-pro.yml", selected)
        self.assertEqual(bundle, select_evidence(self.inventory, self.repository, purpose="semantic"))


if __name__ == "__main__":
    unittest.main()
