"""Tests for shared-resource relationships derived from configuration."""

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
from landscape_core.config_links import RELATIONSHIP_TYPE, propose_shared_resource_relationships  # noqa: E402
from landscape_core.discovery import discover  # noqa: E402
from landscape_core.evidence import select_evidence  # noqa: E402

ANALYZED_AT = "2026-10-28T10:00:00Z"
DRAFT = json.loads((ROOT / "examples" / "contracts" / "questionnaire-draft.valid.json").read_text(encoding="utf-8"))


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def make_repository(base, name, properties):
    repository = base / name
    resources = repository / "src" / "main" / "resources"
    resources.mkdir(parents=True)
    (resources / "application.properties").write_text(properties, encoding="utf-8")
    git(repository, "init", "-q")
    git(repository, "config", "user.name", "Test")
    git(repository, "config", "user.email", "test@example.invalid")
    git(repository, "add", ".")
    git(repository, "commit", "-q", "-m", "Fixture")
    return repository


def application(bundle):
    entry = bundle["selectedEvidence"][0]
    evidence = {"kind": "repository", "repositoryId": bundle["repository"], "commit": bundle["commit"],
                "path": entry["path"], "lines": entry["lines"], "observationId": entry["observationIds"][0]}
    return {"id": "app-" + bundle["repository"], "name": bundle["repository"], "purpose": "Synthetic application.",
            "assessment": {"status": "inferred", "confidence": "medium", "analyzedAt": ANALYZED_AT,
                           "evidence": [evidence], "counterevidence": []}}


class ConfigLinkTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.base = Path(directory.name)
        self.repositories = {
            "service-a": make_repository(self.base, "service-a",
                                         "kafka.topic.out=orders-merged\nsqs.q.url=https://sqs.eu-west-1.amazonaws.com/123456789012/a-only\n"),
            "service-b": make_repository(self.base, "service-b",
                                         "%pro.in.topic=orders-merged\nsqs.q.url=https://sqs.eu-west-1.amazonaws.com/123456789012/b-only\n"),
            "service-c": make_repository(self.base, "service-c", "kafka.topic.in=other-topic\n"),
        }
        self.inventories = [discover(path, name) for name, path in sorted(self.repositories.items())]
        self.bundles = [select_evidence(inventory, self.repositories[inventory["repository"]], purpose="semantic")
                        for inventory in self.inventories]
        self.proposal = empty_catalog()
        self.proposal["applications"] = [application(bundle) for bundle in self.bundles]

    def link(self, proposal=None):
        return propose_shared_resource_relationships(proposal or self.proposal, self.inventories, self.bundles, ANALYZED_AT)

    def test_shared_topic_becomes_one_valid_inferred_relationship(self):
        result = self.link()
        self.assertEqual([item["id"] for item in result["relationships"]],
                         ["rel-app-service-a-shares-resource-with-app-service-b"])
        relationship = result["relationships"][0]
        self.assertEqual(relationship["type"], RELATIONSHIP_TYPE)
        self.assertEqual(relationship["assessment"]["status"], "inferred")
        self.assertIn("kafka-topic `orders-merged`", relationship["statement"])
        self.assertEqual({item["repositoryId"] for item in relationship["assessment"]["evidence"]},
                         {"service-a", "service-b"})
        self.assertEqual(validate_catalog_proposal(result, DRAFT, self.bundles), [])

    def test_distinct_values_and_unshared_repositories_are_not_linked(self):
        text = json.dumps(self.link()["relationships"])
        self.assertNotIn("service-c", text)
        self.assertNotIn("a-only", text)

    def test_linking_is_idempotent_and_replaces_stale_relationships(self):
        once = self.link()
        self.assertEqual(once, self.link(once))
        stale = json.loads(json.dumps(once))
        stale["relationships"][0]["statement"] = "stale"
        self.assertEqual(once, self.link(stale))

    def test_cli_link_config_writes_the_proposal(self):
        files = {"proposal.json": self.proposal}
        files.update({"inv{}.json".format(i): value for i, value in enumerate(self.inventories)})
        files.update({"ev{}.json".format(i): value for i, value in enumerate(self.bundles)})
        for name, value in files.items():
            (self.base / name).write_text(json.dumps(value), encoding="utf-8")
        result = subprocess.run(
            [str(ROOT / "tools" / "landscape"), "catalog", "link-config", str(self.base / "proposal.json"),
             "--inventory"] + [str(self.base / "inv{}.json".format(i)) for i in range(3)] +
            ["--evidence"] + [str(self.base / "ev{}.json".format(i)) for i in range(3)] +
            ["--analyzed-at", ANALYZED_AT, "--output", str(self.base / "out.json")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.base / "out.json").read_text(encoding="utf-8")), self.link())


if __name__ == "__main__":
    unittest.main()
