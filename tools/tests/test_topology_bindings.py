"""Tests for topology bindings and their use when pairing manifests with applications."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(TOOLS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from landscape_core.catalog_proposals import empty_catalog  # noqa: E402
from landscape_core.config_links import FEED_TYPE, propose_shared_resource_relationships  # noqa: E402
from landscape_core.contracts import validate_source_topology  # noqa: E402
from landscape_core.discovery import discover  # noqa: E402
from landscape_core.evidence import select_evidence  # noqa: E402
from landscape_core.topology_bindings import infer_bindings, selection_pairs  # noqa: E402
from test_env_vars import ANALYZED_AT, CONSUMER, CRONJOB, JAVA, PRODUCER, application, make_repository  # noqa: E402


def topology(*kubernetes):
    selections = [{"id": "consumer-app", "kind": "application", "repositoryId": "consumer", "subpath": "."},
                  {"id": "producer-app", "kind": "application", "repositoryId": "producer", "subpath": "."}]
    selections += [{"id": name, "kind": "kubernetes", "repositoryId": "manifests", "subpath": path} for name, path in kubernetes]
    return {"schemaVersion": 1, "bindings": [],
            "repositories": [{"id": "consumer", "kind": "application"}, {"id": "manifests", "kind": "kubernetes"},
                             {"id": "producer", "kind": "application"}],
            "sourceSelections": sorted(selections, key=lambda item: item["id"])}


def catalog(*repositories):
    document = empty_catalog()
    for repository in repositories:
        evidence = {"kind": "repository", "repositoryId": repository, "commit": "a" * 40, "path": "pom.xml"}
        document["applications"].append({
            "id": "app-" + repository, "name": repository, "purpose": "Synthetic.",
            "assessment": {"status": "inferred", "confidence": "low", "analyzedAt": ANALYZED_AT,
                           "evidence": [evidence], "counterevidence": []}})
    return document


class InferBindingsTest(unittest.TestCase):
    def setUp(self):
        self.topology = topology(("consumer-kubernetes-pro", "env/pro/team/consumer"),
                                 ("jobs-kubernetes-pro", "env/pro/team/job-runner"))
        self.catalog = catalog("consumer", "producer")

    def test_unbound_selections_get_inferred_bindings_by_convention(self):
        result = infer_bindings(self.topology, self.catalog)
        bound = {item["sourceSelectionId"]: (item["target"]["id"], item["status"]) for item in result["bindings"]}
        self.assertEqual(bound, {"consumer-app": ("app-consumer", "inferred"),
                                 "consumer-kubernetes-pro": ("app-consumer", "inferred"),
                                 "producer-app": ("app-producer", "inferred")})
        self.assertEqual(validate_source_topology(result, None, self.catalog), [])

    def test_inference_is_idempotent_and_never_touches_declared_bindings(self):
        declared = copy.deepcopy(self.topology)
        declared["bindings"].append({"sourceSelectionId": "jobs-kubernetes-pro", "target": {"type": "application", "id": "app-consumer"},
                                     "status": "declared", "provenance": {"method": "manual", "detail": "Reviewed by the team."}})
        once = infer_bindings(declared, self.catalog)
        self.assertEqual(once, infer_bindings(once, self.catalog))
        kept = [item for item in once["bindings"] if item["sourceSelectionId"] == "jobs-kubernetes-pro"]
        self.assertEqual([item["status"] for item in kept], ["declared"])

    def test_deployable_bindings_resolve_to_their_application(self):
        proposal = catalog("consumer")
        proposal["relationships"].append({"type": "application-comprises-deployable",
                                          "source": {"entityType": "application", "entityId": "app-consumer"},
                                          "target": {"entityType": "deployable", "entityId": "deploy-consumer"}})
        document = {"bindings": [{"sourceSelectionId": "x", "target": {"type": "deployable", "id": "deploy-consumer"},
                                  "status": "declared", "provenance": {"method": "manual", "detail": "d"}}]}
        self.assertEqual(selection_pairs(document, proposal), {"x": ({"consumer"}, True)})


class BoundPairingTest(unittest.TestCase):
    def build(self):
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
        manifests = make_repository(base, "manifests", {"env/pro/team/job-runner/cronjob.yaml": CRONJOB})
        self.topology = topology(("jobs-kubernetes-pro", "env/pro/team/job-runner"))
        inventories = [discover(path, name) for name, path in sorted(repositories.items())]
        inventories.append(discover(manifests, "manifests", topology=self.topology, selection="jobs-kubernetes-pro"))
        bundles = [select_evidence(item, manifests) if item["repository"] == "manifests"
                   else select_evidence(item, repositories[item["repository"]], purpose="semantic") for item in inventories]
        proposal = empty_catalog()
        proposal["applications"] = [application(bundle) for bundle in bundles if bundle["repository"] != "manifests"]
        return proposal, inventories, bundles

    def feeds(self, proposal, inventories, bundles, topology_document=None):
        result = propose_shared_resource_relationships(proposal, inventories, bundles, ANALYZED_AT, topology_document)
        return [item for item in result["relationships"] if item["type"] == FEED_TYPE]

    def bound(self, status):
        document = copy.deepcopy(self.topology)
        document["bindings"] = [{"sourceSelectionId": "jobs-kubernetes-pro", "target": {"type": "application", "id": "app-consumer"},
                                 "status": status, "provenance": {
                                     "method": "manual" if status == "declared" else "folder-convention", "detail": "d"}}]
        return document

    def test_a_declared_binding_pairs_manifests_whose_folder_name_differs(self):
        proposal, inventories, bundles = self.build()
        self.assertEqual(self.feeds(proposal, inventories, bundles), [])
        feeds = self.feeds(proposal, inventories, bundles, self.bound("declared"))
        self.assertEqual([item["id"] for item in feeds], ["rel-app-producer-feeds-app-consumer"])
        self.assertFalse(any("convention" in text for text in feeds[0]["assessment"]["missingEvidence"]))

    def test_an_inferred_binding_keeps_the_convention_caveat(self):
        proposal, inventories, bundles = self.build()
        feeds = self.feeds(proposal, inventories, bundles, self.bound("inferred"))
        self.assertTrue(any("convention" in text for text in feeds[0]["assessment"]["missingEvidence"]))


class PerEnvironmentNamesTest(unittest.TestCase):
    def test_a_target_named_differently_per_profile_yields_one_match_per_name(self):
        from landscape_core.io_links import Resolved, feed_matches
        import landscape_core.io_links as links

        def point(repository, role):
            return {"repository": repository, "value": {"role": role}, "id": role}
        producer = Resolved(point("a", "producer"), "sqs-queue", {"dev": "dev-q", "pro": "pro-q"}, [])
        consumer = Resolved(point("b", "consumer"), "sqs-queue", {"dev": "dev-q", "pro": "pro-q"}, [])
        original = links.resolve_points
        links.resolve_points = lambda inventories, pairing=None, draft=None: [producer, consumer]
        try:
            matches = feed_matches([], None)[("a", "b")]
        finally:
            links.resolve_points = original
        self.assertEqual([(item["identity"], item["profiles"]) for item in matches], [("dev-q", ["dev"]), ("pro-q", ["pro"])])


class InferBindingsCliTest(unittest.TestCase):
    def test_cli_writes_a_valid_topology(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "topology.json").write_text(json.dumps(topology(("consumer-kubernetes-pro", "env/pro/team/consumer"))), encoding="utf-8")
            (base / "catalog.json").write_text(json.dumps(catalog("consumer", "producer")), encoding="utf-8")
            run = subprocess.run([str(ROOT / "tools" / "landscape"), "topology", "infer-bindings", str(base / "topology.json"),
                                  "--catalog", str(base / "catalog.json"), "--output", str(base / "out.json")],
                                 capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(len(json.loads((base / "out.json").read_text(encoding="utf-8"))["bindings"]), 3)


if __name__ == "__main__":
    unittest.main()
