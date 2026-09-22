"""Contract tests for the first synthetic vertical-slice boundary."""

import json
from pathlib import Path
import sys
import unittest


TOOLS = Path(__file__).resolve().parents[1]
PROJECT = TOOLS.parent
FIXTURES = PROJECT / "examples" / "contracts"
sys.path.insert(0, str(TOOLS))

from landscape_core.contracts import (  # noqa: E402
    artifact_id,
    validate_api_observation,
    validate_candidate_envelope,
    validate_container_observation,
    validate_evidence_bundle,
    validate_kafka_observation,
    validate_landscape_catalog,
    validate_manifest_observation,
    validate_source_registry,
    validate_source_resolution,
    validate_source_topology,
)
from landscape_core.candidates import validate_candidate  # noqa: E402
from landscape_core.observations import observation  # noqa: E402
from landscape_core.validation import validate_inventory  # noqa: E402


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ContractTest(unittest.TestCase):
    def test_all_json_contract_artifacts_parse(self):
        for path in sorted((PROJECT / "schemas").glob("*.json")):
            with self.subTest(path=path.name):
                json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(FIXTURES.glob("*.json")):
            with self.subTest(path=path.name):
                json.loads(path.read_text(encoding="utf-8"))

    def test_manifest_observation_examples(self):
        for index, item in enumerate(load("manifest-observations.valid.json")):
            self.assertEqual([], validate_manifest_observation(item), index)
        for index, item in enumerate(load("manifest-observations.invalid.json")):
            self.assertTrue(validate_manifest_observation(item), index)

    def test_api_observation_examples(self):
        for index, item in enumerate(load("api-observations.valid.json")):
            self.assertEqual([], validate_api_observation(item), index)
        for index, item in enumerate(load("api-observations.invalid.json")):
            self.assertTrue(validate_api_observation(item), index)
        yaml_document = load("api-observations.valid.json")[0]
        yaml_document["value"]["serialization"] = "yaml"
        self.assertTrue(validate_api_observation(yaml_document))
        unknown_yaml_gap = load("api-observations.valid.json")[-1]
        unknown_yaml_gap["value"]["specification"] = "unknown"
        self.assertTrue(validate_api_observation(unknown_yaml_gap))

    def test_api_observation_lines_and_literals_are_part_of_identity(self):
        value = load("api-observations.valid.json")[1]["value"]
        common = {
            "kind": "api-operation",
            "repository": "synthetic-java-service",
            "commit": "a" * 40,
            "detector": "api-contract",
            "detector_version": 1,
            "source_path": "src/main/resources/openapi.json",
        }
        first = observation(source_lines="8-10", value=value, **common)
        moved = observation(source_lines="9-11", value=value, **common)
        changed = observation(
            source_lines="8-10", value=dict(value, target="/customers"), **common
        )
        self.assertEqual(3, len({first["id"], moved["id"], changed["id"]}))

    def test_kafka_observation_examples(self):
        for index, item in enumerate(load("kafka-observations.valid.json")):
            self.assertEqual([], validate_kafka_observation(item), index)
        for index, item in enumerate(load("kafka-observations.invalid.json")):
            self.assertTrue(validate_kafka_observation(item), index)

    def test_kafka_observation_lines_and_literals_are_part_of_identity(self):
        common = {
            "kind": "kafka-topic-reference",
            "repository": "synthetic-kotlin-service",
            "commit": "a" * 40,
            "detector": "kafka-literals",
            "detector_version": 1,
            "source_path": (
                "src/main/kotlin/com/example/mercurio/customer/"
                "CustomerEventPublisher.kt"
            ),
        }
        value = {
            "role": "producer-send",
            "form": "kafka-template-send",
            "topic": "customer-updated",
        }
        first = observation(source_lines="12", value=value, **common)
        moved = observation(source_lines="13", value=value, **common)
        changed = observation(
            source_lines="12",
            value=dict(value, topic="customer-created"),
            **common
        )
        self.assertEqual(3, len({first["id"], moved["id"], changed["id"]}))

    def test_container_observation_examples(self):
        for index, item in enumerate(load("container-observations.valid.json")):
            self.assertEqual([], validate_container_observation(item), index)
        for index, item in enumerate(load("container-observations.invalid.json")):
            self.assertTrue(validate_container_observation(item), index)
        malformed_role = load("container-observations.valid.json")[0]
        malformed_role["value"]["role"] = []
        self.assertTrue(validate_container_observation(malformed_role))
        malformed_code = load("container-observations.valid.json")[-1]
        malformed_code["value"]["code"] = []
        self.assertTrue(validate_container_observation(malformed_code))

    def test_container_observation_lines_aliases_and_literals_are_identity(self):
        common = {
            "kind": "container-image-reference",
            "repository": "synthetic-java-service",
            "commit": "a" * 40,
            "detector": "container-images",
            "detector_version": 1,
            "source_path": "Dockerfile",
        }
        value = {
            "role": "base-image",
            "form": "dockerfile-from",
            "image": "example/builder:1",
            "stageAlias": "builder",
        }
        first = observation(source_lines="1", value=value, **common)
        moved = observation(source_lines="2", value=value, **common)
        changed_image = observation(
            source_lines="1", value=dict(value, image="example/builder:2"), **common
        )
        changed_alias = observation(
            source_lines="1", value=dict(value, stageAlias="compile"), **common
        )
        self.assertEqual(
            4, len({first["id"], moved["id"], changed_image["id"], changed_alias["id"]})
        )

    def test_observation_inventory_examples(self):
        self.assertEqual([], validate_inventory(load("observation-inventory.valid.json")))
        self.assertTrue(validate_inventory(load("observation-inventory.invalid.json")))

    def test_manifest_observation_lines_are_part_of_identity(self):
        item = load("manifest-observations.valid.json")[0]
        first = observation(
            kind=item["kind"],
            repository="synthetic-java-service",
            commit="a" * 40,
            detector="maven",
            detector_version=1,
            source_path=item["source"]["path"],
            source_lines=item["source"]["lines"],
            value=item["value"],
        )
        second = dict(first)
        second["source"] = dict(first["source"], lines="4-6")
        inventory = {
            "schemaVersion": 1,
            "repository": "synthetic-java-service",
            "commit": "a" * 40,
            "detectors": [{"name": "maven", "version": 1}],
            "observations": [second],
            "excluded": [],
        }
        errors = validate_inventory(inventory)
        self.assertTrue(any("id does not match its content" in error for error in errors))

    def test_source_registry_examples(self):
        self.assertEqual([], validate_source_registry(load("source-registry.valid.json")))
        self.assertTrue(validate_source_registry(load("source-registry.invalid.json")))

    def test_source_resolution_examples(self):
        self.assertEqual([], validate_source_resolution(load("source-resolution.valid.json")))
        self.assertTrue(validate_source_resolution(load("source-resolution.invalid.json")))

    def test_evidence_bundle_examples(self):
        valid = load("evidence-bundle.valid.json")
        self.assertEqual(artifact_id(valid), valid["id"])
        self.assertEqual([], validate_evidence_bundle(valid))
        self.assertTrue(validate_evidence_bundle(load("evidence-bundle.invalid.json")))

    def test_candidate_examples_and_bundle_binding(self):
        bundle = load("evidence-bundle.valid.json")
        candidate = load("candidate-envelope.valid.json")
        self.assertEqual([], validate_candidate_envelope(candidate, bundle))
        self.assertTrue(
            validate_candidate_envelope(load("candidate-envelope.invalid.json"), bundle)
        )

    def test_landscape_catalog_examples(self):
        self.assertEqual([], validate_landscape_catalog(
            load("landscape-catalog.valid.json")
        ))
        self.assertTrue(validate_landscape_catalog(
            load("landscape-catalog.invalid.json")
        ))

    def test_source_topology_examples_and_reference_binding(self):
        topology = load("source-topology.valid.json")
        sources = {
            "schemaVersion": 1,
            "repositories": [
                {
                    "enabled": True,
                    "id": item["id"],
                    "kind": item["kind"],
                    "path": "/tmp/bounded-knowledge/" + item["id"],
                }
                for item in topology["repositories"]
            ],
        }
        catalog = load("landscape-catalog.valid.json")
        self.assertEqual([], validate_source_topology(topology, sources, catalog))
        self.assertTrue(validate_source_topology(
            load("source-topology.invalid.json"), sources, catalog
        ))

        shared = [
            item for item in topology["bindings"]
            if item["sourceSelectionId"] == "shared-team-workloads"
        ]
        self.assertEqual(4, len(shared))
        self.assertEqual(
            {"customer-management", "order-management", "customer-api", "order-api"},
            {item["target"]["id"] for item in shared},
        )

    def test_catalog_and_topology_reject_invalid_references_and_ordering(self):
        catalog = load("landscape-catalog.valid.json")
        catalog["relationships"][0]["target"]["entityId"] = "missing-deployable"
        self.assertTrue(any(
            "unknown entity" in error
            for error in validate_landscape_catalog(catalog)
        ))

        topology = load("source-topology.valid.json")
        topology["sourceSelections"] = list(reversed(topology["sourceSelections"]))
        sources = {
            "schemaVersion": 1,
            "repositories": [
                {
                    "enabled": True,
                    "id": item["id"],
                    "kind": item["kind"],
                    "path": "/tmp/bounded-knowledge/" + item["id"],
                }
                for item in topology["repositories"]
            ],
        }
        errors = validate_source_topology(
            topology, sources, load("landscape-catalog.valid.json")
        )
        self.assertTrue(any("sourceSelections must be sorted" in error for error in errors))

    def test_catalog_and_topology_nested_malformed_values_fail_closed(self):
        catalog_cases = (
            lambda value: value["applications"][0]["assessment"].update(status=[]),
            lambda value: value["deployables"][0].update(kind=[]),
            lambda value: value["relationships"][0].update(type=[]),
            lambda value: value["relationships"][0]["source"].update(entityType=[]),
        )
        for mutate in catalog_cases:
            catalog = load("landscape-catalog.valid.json")
            mutate(catalog)
            self.assertTrue(validate_landscape_catalog(catalog))

        topology_cases = (
            lambda value: value["repositories"][0].update(kind=[]),
            lambda value: value["sourceSelections"][0].update(repositoryId=[]),
            lambda value: value["bindings"][0]["target"].update(type=[]),
            lambda value: value["bindings"][0].update(status=[]),
        )
        for mutate in topology_cases:
            topology = load("source-topology.valid.json")
            mutate(topology)
            self.assertTrue(validate_source_topology(topology))

    def test_catalog_and_topology_operational_rules_match_schema_constraints(self):
        catalog = load("landscape-catalog.valid.json")
        catalog["schemaVersion"] = True
        self.assertTrue(validate_landscape_catalog(catalog))

        catalog = load("landscape-catalog.valid.json")
        catalog["strategicCapabilities"][0]["desiredOutcomes"] = []
        self.assertTrue(validate_landscape_catalog(catalog))

        catalog = load("landscape-catalog.valid.json")
        unknown = next(
            item for item in catalog["relationships"]
            if item["assessment"]["status"] == "unknown"
        )
        unknown["assessment"]["missingEvidence"] = []
        self.assertTrue(validate_landscape_catalog(catalog))

        for subpath in ("team folder", "team:folder", "tëam/folder"):
            topology = load("source-topology.valid.json")
            topology["sourceSelections"][0]["subpath"] = subpath
            self.assertTrue(validate_source_topology(topology), subpath)

        topology = load("source-topology.valid.json")
        topology["schemaVersion"] = True
        self.assertTrue(validate_source_topology(topology))

    def test_strategy_requires_curated_support(self):
        catalog = load("landscape-catalog.valid.json")
        capability = catalog["strategicCapabilities"][0]
        capability["assessment"]["evidence"] = [
            load("landscape-catalog.valid.json")["applications"][0]["assessment"][
                "evidence"
            ][0]
        ]
        self.assertTrue(any(
            "requires curated supporting evidence" in error
            for error in validate_landscape_catalog(catalog)
        ))

        catalog = load("landscape-catalog.valid.json")
        relationship = next(
            item for item in catalog["relationships"]
            if item["type"] == "application-contributes-to-strategic-capability"
        )
        relationship["assessment"]["evidence"] = [
            catalog["applications"][0]["assessment"]["evidence"][0]
        ]
        self.assertTrue(any(
            "requires curated supporting evidence" in error
            for error in validate_landscape_catalog(catalog)
        ))

    def test_topology_rejects_dangling_catalog_evidence_references(self):
        topology = load("source-topology.valid.json")
        sources = {
            "schemaVersion": 1,
            "repositories": [
                {
                    "enabled": True,
                    "id": item["id"],
                    "kind": item["kind"],
                    "path": "/tmp/bounded-knowledge/" + item["id"],
                }
                for item in topology["repositories"]
            ],
        }
        catalog = load("landscape-catalog.valid.json")
        evidence = catalog["applications"][0]["assessment"]["evidence"][0]
        evidence["repositoryId"] = "missing-repository"
        evidence["sourceSelectionId"] = "missing-selection"
        errors = validate_source_topology(topology, sources, catalog)
        self.assertTrue(any("unknown topology repository" in error for error in errors))
        self.assertTrue(any("unknown source selection" in error for error in errors))

    def test_candidate_validation_accepts_a_verified_evidence_subrange(self):
        bundle = load("evidence-bundle.valid.json")
        bundle["selectedEvidence"][0]["lines"] = "5-10"
        bundle["id"] = artifact_id(bundle)
        candidate = load("candidate-envelope.valid.json")
        candidate["evidenceBundleId"] = bundle["id"]
        candidate["proposedProfile"]["claims"][0]["evidence"][0]["lines"] = "6"

        self.assertEqual([], validate_candidate(candidate, bundle))

    def test_candidate_validation_rejects_unverifiable_references_and_statuses(self):
        bundle = load("evidence-bundle.valid.json")
        cases = {
            "kind": lambda candidate: candidate.update(kind="terraform"),
            "path": lambda candidate: candidate["proposedProfile"]["claims"][0][
                "evidence"
            ][0].update(path="src/Other.java"),
            "commit": lambda candidate: candidate["proposedProfile"]["claims"][0][
                "evidence"
            ][0].update(commit="b" * 40),
            "observation": lambda candidate: candidate["proposedProfile"]["claims"][0][
                "evidence"
            ][0].update(observationId="d" * 64),
            "lines": lambda candidate: candidate["proposedProfile"]["claims"][0][
                "evidence"
            ][0].update(lines="6"),
            "status": lambda candidate: candidate["proposedProfile"]["claims"][0].update(
                status="asserted"
            ),
            "malformed-status": lambda candidate: candidate["proposedProfile"]["claims"][
                0
            ].update(status=[]),
            "malformed-path": lambda candidate: candidate["proposedProfile"]["claims"][0][
                "evidence"
            ][0].update(path=[]),
            "malformed-observation": lambda candidate: candidate["proposedProfile"][
                "claims"
            ][0]["evidence"][0].update(observationId=[]),
        }
        for name, mutate in cases.items():
            candidate = load("candidate-envelope.valid.json")
            mutate(candidate)
            with self.subTest(name=name):
                self.assertTrue(validate_candidate(candidate, bundle))

        candidate = load("candidate-envelope.valid.json")
        claim = candidate["proposedProfile"]["claims"][0]
        claim["status"] = "inferred"
        claim["evidence"] = []
        self.assertTrue(any(
            "inferred claims require evidence" in error
            for error in validate_candidate(candidate, bundle)
        ))

    def test_candidate_validation_rejects_an_invalid_evidence_bundle_first(self):
        bundle = load("evidence-bundle.valid.json")
        bundle["selectedEvidence"][0]["path"] = "../pom.xml"
        errors = validate_candidate(load("candidate-envelope.valid.json"), bundle)
        self.assertTrue(errors)
        self.assertTrue(all(error.startswith("evidence bundle: ") for error in errors))

    def test_malformed_top_level_values_fail_closed(self):
        validators = (
            validate_manifest_observation,
            validate_source_registry,
            validate_source_resolution,
            validate_evidence_bundle,
            validate_candidate_envelope,
            validate_landscape_catalog,
            validate_source_topology,
        )
        for validator in validators:
            for value in (None, [], "invalid", 1):
                with self.subTest(validator=validator.__name__, value=value):
                    self.assertTrue(validator(value))


if __name__ == "__main__":
    unittest.main()
