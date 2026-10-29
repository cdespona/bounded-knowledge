"""Tests for allowlisted container environment variables and their use in producer/consumer links."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))

from landscape_core.catalog_proposals import empty_catalog, validate_catalog_proposal  # noqa: E402
from landscape_core.config_files import mask_account_ids  # noqa: E402
from landscape_core.config_links import FEED_TYPE, propose_shared_resource_relationships  # noqa: E402
from landscape_core.discovery import discover  # noqa: E402
from landscape_core.env_vars import classify_env, describe_env, environment_names  # noqa: E402
from landscape_core.evidence import select_evidence  # noqa: E402
from landscape_core.validation import validate_inventory  # noqa: E402

ANALYZED_AT = "2026-10-29T10:00:00Z"
DRAFT = json.loads((ROOT / "examples" / "contracts" / "questionnaire-draft.valid.json").read_text(encoding="utf-8"))
JAVA = "src/main/java/com/example/"

CRONJOB = """apiVersion: batch/v1
kind: CronJob
metadata:
  name: consumer
spec:
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: consumer
              image: registry.example.invalid/team/consumer:1.0
              env:
                - name: ENVIRONMENT
                  value: pro
                - name: QUEUE_URL
                  value: https://sqs.eu-west-1.amazonaws.com/123456789012/pro-events
                - name: JAVA_TOOL_OPTIONS
                  value: "-XX:+UseG1GC"
                - name: DATABASE_SECRET_STRING
                  value: leaked-secret
                - name: JDBC_PASSWORD
                  value: hunter2
                - name: DB_JDBC_URL
                  value: jdbc:oracle://user:pw@db.example.invalid/svc
                - name: KAFKA_BOOTSTRAP
                  valueFrom:
                    secretKeyRef:
                      name: kafka
                      key: bootstrap
                - name: EMPTY_QUEUE_URL
                  value: ""
"""


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def make_repository(base, name, files):
    repository = base / name
    for relative, text in files.items():
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    git(repository, "init", "-q")
    git(repository, "config", "user.name", "Test")
    git(repository, "config", "user.email", "test@example.invalid")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "Fixture")
    return repository


def topology(*selections):
    return {"schemaVersion": 1, "bindings": [],
            "repositories": [{"id": "consumer", "kind": "application"}, {"id": "manifests", "kind": "kubernetes"},
                             {"id": "producer", "kind": "application"}],
            "sourceSelections": [{"id": selection, "kind": "kubernetes", "repositoryId": "manifests", "subpath": path}
                                 for selection, path in selections]}


class EnvPolicyTest(unittest.TestCase):
    def test_only_infrastructure_names_are_classified(self):
        self.assertEqual(classify_env("QUEUE_URL", "x"), "sqs")
        self.assertEqual(classify_env("AUDIT_BUCKET", "x"), "s3")
        self.assertIsNone(classify_env("JAVA_TOOL_OPTIONS", "-Xmx1g"))
        self.assertIsNone(classify_env("WAF_HEADER_SECRET_KEY", "value"))

    def test_sensitive_names_and_credential_values_are_withheld(self):
        self.assertEqual(describe_env("JDBC_PASSWORD", "hunter2"), ("withheld", None))
        self.assertEqual(describe_env("DB_URL", "jdbc:oracle://user:pw@host/svc"), ("withheld", None))
        self.assertEqual(describe_env("QUEUE_URL", "https://sqs.eu-west-1.amazonaws.com/123456789012/q"),
                         ("literal", "https://sqs.eu-west-1.amazonaws.com/<aws-account>/q"))

    def test_registry_account_ids_are_masked(self):
        self.assertEqual(mask_account_ids("image: 123456789012.dkr.ecr.eu-west-1.amazonaws.com/team/app:1"),
                         "image: <aws-account>.dkr.ecr.eu-west-1.amazonaws.com/team/app:1")

    def test_relaxed_binding_names(self):
        self.assertEqual(environment_names("sqs.loader-events.url"), {"sqs.loader-events.url", "SQS_LOADER_EVENTS_URL"})


class EnvDetectorTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository = make_repository(Path(directory.name), "manifests", {"env/pro/team/consumer/cronjob.yaml": CRONJOB})
        self.topology = topology(("consumer-kubernetes-pro", "env/pro/team/consumer"))
        self.inventory = discover(self.repository, "manifests", topology=self.topology, selection="consumer-kubernetes-pro")
        self.variables = {item["value"]["name"]: item for item in self.inventory["observations"]
                          if item["kind"] == "env-variable"}

    def test_records_only_infrastructure_variables_and_validates(self):
        self.assertEqual(validate_inventory(self.inventory, source=self.repository), [])
        self.assertEqual(sorted(self.variables), ["DB_JDBC_URL", "EMPTY_QUEUE_URL", "JDBC_PASSWORD",
                                                  "KAFKA_BOOTSTRAP", "QUEUE_URL"])

    def test_values_are_literal_withheld_reference_or_empty(self):
        dispositions = {name: item["value"]["disposition"] for name, item in self.variables.items()}
        self.assertEqual(dispositions, {"DB_JDBC_URL": "withheld", "EMPTY_QUEUE_URL": "empty", "JDBC_PASSWORD": "withheld",
                                        "KAFKA_BOOTSTRAP": "reference", "QUEUE_URL": "literal"})
        self.assertEqual(self.variables["KAFKA_BOOTSTRAP"]["value"]["reference"], "secretKeyRef")
        self.assertEqual(self.variables["QUEUE_URL"]["value"]["value"],
                         "https://sqs.eu-west-1.amazonaws.com/<aws-account>/pro-events")
        self.assertNotIn("hunter2", json.dumps(self.inventory))
        self.assertNotIn("leaked-secret", json.dumps(self.inventory))

    def test_evidence_masks_withheld_values_and_accounts(self):
        bundle = select_evidence(self.inventory, self.repository)
        text = "\n".join(entry["content"] for entry in bundle["selectedEvidence"])
        for hidden in ("hunter2", "user:pw", "123456789012", "leaked-secret"):
            self.assertNotIn(hidden, text)
        self.assertIn("<withheld>", text)
        self.assertIn("<aws-account>", text)


PRODUCER = """package com.example;
import software.amazon.awssdk.services.sqs.SqsClient;
class Sender {
    @ConfigProperty(name = "queue.url") String url;
    void send() { SendMessageRequest.builder().queueUrl(url).build(); }
}
"""

CONSUMER = """package com.example;
import software.amazon.awssdk.services.sqs.SqsClient;
class Receiver {
    Receiver(@Value("${QUEUE_URL}") String url) { this.url = url; }
    void receive() { ReceiveMessageRequest.builder().queueUrl(url).build(); }
}
"""


def application(bundle):
    entry = bundle["selectedEvidence"][0]
    evidence = {"kind": "repository", "repositoryId": bundle["repository"], "commit": bundle["commit"],
                "path": entry["path"], "lines": entry["lines"], "observationId": entry["observationIds"][0]}
    return {"id": "app-" + bundle["repository"], "name": bundle["repository"], "purpose": "Synthetic application.",
            "assessment": {"status": "inferred", "confidence": "medium", "analyzedAt": ANALYZED_AT,
                           "evidence": [evidence], "counterevidence": []}}


class EnvironmentLinkTest(unittest.TestCase):
    def build(self, queue_name):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        resources = "src/main/resources/"
        repositories = {
            "producer": make_repository(base, "producer", {
                JAVA + "Sender.java": PRODUCER,
                resources + "application.properties": "queue.url=https://sqs.eu-west-1.amazonaws.com/111111111111/pro-events\n"}),
            "consumer": make_repository(base, "consumer", {JAVA + "Receiver.java": CONSUMER}),
        }
        manifests = make_repository(base, "manifests", {
            "env/pro/team/consumer/cronjob.yaml": CRONJOB.replace("pro-events", queue_name)})
        selection = topology(("consumer-kubernetes-pro", "env/pro/team/consumer"))
        inventories = [discover(path, name) for name, path in sorted(repositories.items())]
        inventories.append(discover(manifests, "manifests", topology=selection, selection="consumer-kubernetes-pro"))
        bundles = [select_evidence(inventory, manifests) if inventory["repository"] == "manifests"
                   else select_evidence(inventory, repositories[inventory["repository"]], purpose="semantic")
                   for inventory in inventories]
        proposal = empty_catalog()
        proposal["applications"] = [application(bundle) for bundle in bundles if bundle["repository"] != "manifests"]
        return propose_shared_resource_relationships(proposal, inventories, bundles, ANALYZED_AT), bundles

    def test_environment_value_pairs_producer_and_consumer(self):
        result, bundles = self.build("pro-events")
        feeds = [item for item in result["relationships"] if item["type"] == FEED_TYPE]
        self.assertEqual([item["id"] for item in feeds], ["rel-app-producer-feeds-app-consumer"])
        self.assertIn("sqs-queue `pro-events` (profiles: pro)", feeds[0]["statement"])
        self.assertTrue(any(item["repositoryId"] == "manifests" for item in feeds[0]["assessment"]["evidence"]))
        self.assertTrue(any("folder convention" in text for text in feeds[0]["assessment"]["missingEvidence"]))
        self.assertEqual(validate_catalog_proposal(result, DRAFT, bundles), [])

    def test_different_queue_names_are_not_linked(self):
        result, _ = self.build("other-events")
        self.assertEqual([item for item in result["relationships"] if item["type"] == FEED_TYPE], [])


if __name__ == "__main__":
    unittest.main()
