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
    validate_candidate_envelope,
    validate_evidence_bundle,
    validate_manifest_observation,
    validate_source_registry,
    validate_source_resolution,
)
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
        canonical = json.loads((PROJECT / "sources.json").read_text(encoding="utf-8"))
        self.assertEqual([], validate_source_registry(canonical))

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

    def test_malformed_top_level_values_fail_closed(self):
        validators = (
            validate_manifest_observation,
            validate_source_registry,
            validate_source_resolution,
            validate_evidence_bundle,
            validate_candidate_envelope,
        )
        for validator in validators:
            for value in (None, [], "invalid", 1):
                with self.subTest(validator=validator.__name__, value=value):
                    self.assertTrue(validator(value))


if __name__ == "__main__":
    unittest.main()
