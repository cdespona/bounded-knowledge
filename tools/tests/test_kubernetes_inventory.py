"""Focused tests for bounded Kubernetes YAML container-image discovery."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


TOOLS = Path(__file__).resolve().parents[1]
PROJECT = TOOLS.parent
LANDSCAPE = TOOLS / "landscape"
sys.path.insert(0, str(TOOLS))

from landscape_core.discovery import discover  # noqa: E402
from landscape_core.evidence import select_evidence  # noqa: E402
from landscape_core.contracts import validate_evidence_bundle  # noqa: E402
from landscape_core.safety import MAX_FILE_BYTES  # noqa: E402
from landscape_core.validation import validate_inventory  # noqa: E402


REPOSITORY_ID = "k8s-manifests"
SELECTION_ID = "order-api-production-kubernetes"
SELECTION_SUBPATH = "k8s-capside/pro/mango/orders/order-api"


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


class KubernetesImageInventoryTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.base = Path(self.temporary_directory.name)
        source = PROJECT / "examples" / "synthetic-kubernetes-manifests"
        self.repository = self.base / "synthetic-kubernetes-manifests"
        shutil.copytree(str(source), str(self.repository))
        git(self.repository, "init", "-q")
        git(self.repository, "config", "user.name", "Mercurio Test")
        git(self.repository, "config", "user.email", "mercurio@example.invalid")
        git(self.repository, "add", ".")
        git(self.repository, "commit", "-q", "-m", "Synthetic Kubernetes fixture")
        self.topology_path = self.repository / "source-topology.json"
        self.topology = json.loads(self.topology_path.read_text(encoding="utf-8"))
        self.selection_root = self.repository / SELECTION_SUBPATH

    def commit(self, message="Add Kubernetes inventory test case"):
        git(self.repository, "add", ".")
        git(self.repository, "commit", "-q", "-m", message)

    def inventory(self, topology=None, selection=SELECTION_ID):
        return discover(
            self.repository,
            REPOSITORY_ID,
            topology=self.topology if topology is None else topology,
            selection=selection,
        )

    @staticmethod
    def kubernetes_observations(inventory):
        return [
            item
            for item in inventory["observations"]
            if item["detector"]["name"] == "kubernetes-images"
        ]

    @classmethod
    def images(cls, inventory):
        return [
            item
            for item in cls.kubernetes_observations(inventory)
            if item["kind"] == "container-image-reference"
        ]

    @classmethod
    def gaps(cls, inventory):
        return [
            item
            for item in cls.kubernetes_observations(inventory)
            if item["kind"] == "container-gap"
        ]

    def write_candidate(self, name, content):
        path = self.selection_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_fixture_covers_supported_workloads_lists_and_container_categories(self):
        inventory = self.inventory()
        self.assertEqual([], validate_inventory(inventory, source=self.repository))
        self.assertEqual(
            {"git", "kubernetes-images"},
            {item["name"] for item in inventory["detectors"]},
        )
        self.assertEqual(
            {
                "id": SELECTION_ID,
                "kind": "kubernetes",
                "repositoryId": REPOSITORY_ID,
                "subpath": SELECTION_SUBPATH,
            },
            inventory["sourceSelection"],
        )

        images = self.images(inventory)
        self.assertEqual(19, len(images))
        self.assertEqual(
            {"Pod", "Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job", "CronJob"},
            {item["value"]["workloadKind"] for item in images},
        )
        self.assertEqual(
            {"containers", "initContainers", "ephemeralContainers"},
            {item["value"]["containerCategory"] for item in images},
        )
        self.assertTrue(all(
            item["value"]["role"] == "workload-image"
            and item["value"]["form"] == "kubernetes-yaml"
            and item["value"]["sourceSelectionId"] == SELECTION_ID
            for item in images
        ))
        self.assertFalse(self.gaps(inventory))

    def test_multidocument_lines_indexes_and_pointers_are_exact(self):
        images = self.images(self.inventory())
        app = next(
            item for item in images
            if item["value"]["image"] == "registry.example.invalid/orders/api:1"
        )
        self.assertEqual("8", app["source"]["lines"])
        self.assertEqual(0, app["value"]["documentIndex"])
        self.assertEqual("/spec/containers/0/image", app["value"]["pointer"])

        deployment = next(
            item for item in images
            if item["value"]["image"] == "registry.example.invalid/orders/api:2"
        )
        self.assertEqual("25", deployment["source"]["lines"])
        self.assertEqual(1, deployment["value"]["documentIndex"])
        self.assertEqual(
            "/spec/template/spec/containers/0/image",
            deployment["value"]["pointer"],
        )

        repeated = [
            item for item in images
            if item["value"]["image"] == "registry.example.invalid/orders/repeated:1"
        ]
        self.assertEqual(2, len(repeated))
        self.assertEqual({"9"}, {item["source"]["lines"] for item in repeated})
        self.assertEqual(
            {
                "/items/0/spec/template/spec/containers/0/image",
                "/items/0/spec/template/spec/containers/1/image",
            },
            {item["value"]["pointer"] for item in repeated},
        )
        self.assertEqual(2, len({item["id"] for item in repeated}))

    def test_known_non_image_resources_and_non_candidates_are_silent(self):
        observations = self.kubernetes_observations(self.inventory())
        paths = {item["source"]["path"] for item in observations}
        self.assertNotIn(SELECTION_SUBPATH + "/non-images.yaml", paths)
        serialized = json.dumps(observations, sort_keys=True)
        for value in (
            "should-not-be-observed",
            "ignored-json",
            "ignored-text",
            "scaleTargetRef",
        ):
            self.assertNotIn(value, serialized)

    def test_case_insensitive_yaml_suffix_and_recursive_containment(self):
        self.write_candidate(
            "nested/UPPER.YAML",
            "apiVersion: v1\nkind: Pod\nspec:\n  containers:\n    - image: busybox:1\n",
        )
        self.write_candidate(
            "nested/not-yaml.json",
            '{"apiVersion":"v1","kind":"Pod","image":"ignored-json:1"}\n',
        )
        self.commit()
        inventory = self.inventory()
        image = next(item for item in self.images(inventory) if item["value"]["image"] == "busybox:1")
        self.assertEqual(
            SELECTION_SUBPATH + "/nested/UPPER.YAML", image["source"]["path"]
        )
        self.assertNotIn("ignored-json:1", json.dumps(inventory, sort_keys=True))

    def test_unsupported_and_malformed_resources_are_local_gaps(self):
        self.write_candidate(
            "resource-gaps.yaml",
            """apiVersion: v1
kind: List
items:
  - apiVersion: apps/v1beta1
    kind: Deployment
    spec: {template: {spec: {containers: [{image: beta.example/app:1}]}}}
  - apiVersion: example.invalid/v1
    kind: Widget
    spec: {template: {spec: {containers: [{image: guessed.example/app:1}]}}}
  - apiVersion: v1
    kind: ConfigMap
    data: {image: must-not-be-selected}
  - apiVersion: v1
    kind: Secret
    stringData: {password: synthetic-secret-marker}
  - scalar-list-item
  - apiVersion: apps/v1
    kind: Deployment
  - apiVersion: batch/v1
    kind: Job
    spec: {template: {spec: {containers: [{image: valid.example/job:1}]}}}
---
- not-a-resource-object
---
apiVersion: v1
metadata: {name: missing-kind}
""",
        )
        self.commit()
        inventory = self.inventory()
        candidate_items = [
            item for item in self.kubernetes_observations(inventory)
            if item["source"]["path"].endswith("resource-gaps.yaml")
        ]
        self.assertEqual(
            ["valid.example/job:1"],
            [item["value"]["image"] for item in candidate_items if item["kind"] == "container-image-reference"],
        )
        self.assertTrue(
            {
                "unsupported-kubernetes-version",
                "unsupported-kubernetes-kind",
                "invalid-kubernetes-list-item",
                "invalid-kubernetes-workload",
                "invalid-kubernetes-document",
                "missing-kubernetes-type",
            }
            <= {item["value"]["code"] for item in candidate_items if item["kind"] == "container-gap"}
        )
        serialized = json.dumps(candidate_items, sort_keys=True)
        self.assertNotIn("guessed.example", serialized)
        self.assertNotIn("beta.example", serialized)
        self.assertNotIn("must-not-be-selected", serialized)
        self.assertNotIn("synthetic-secret-marker", serialized)

    def test_typed_list_metadata_must_agree_and_independent_items_continue(self):
        self.write_candidate(
            "typed-list-metadata.yaml",
            """apiVersion: apps/v1
kind: DeploymentList
items:
  - apiVersion: batch/v1
    kind: Deployment
    spec: {template: {spec: {containers: [{image: hidden.example/version:1}]}}}
  - apiVersion: apps/v1
    kind: StatefulSet
    spec: {template: {spec: {containers: [{image: hidden.example/kind:1}]}}}
  - apiVersion: 7
    kind: Deployment
    spec: {template: {spec: {containers: [{image: hidden.example/non-string-version:1}]}}}
  - apiVersion: apps/v1
    kind: null
    spec: {template: {spec: {containers: [{image: hidden.example/non-string-kind:1}]}}}
  - apiVersion: apps/v1
    kind: Deployment
    spec: {template: {spec: {containers: [{image: valid.example/deployment:1}]}}}
""",
        )
        self.commit()
        items = [
            item for item in self.kubernetes_observations(self.inventory())
            if item["source"]["path"].endswith("typed-list-metadata.yaml")
        ]
        self.assertEqual(
            ["valid.example/deployment:1"],
            [item["value"]["image"] for item in items if item["kind"] == "container-image-reference"],
        )
        self.assertEqual(
            {"unsupported-kubernetes-version", "unsupported-kubernetes-kind"},
            {item["value"]["code"] for item in items if item["kind"] == "container-gap"},
        )
        serialized = json.dumps(items, sort_keys=True)
        self.assertNotIn("hidden.example", serialized)

    def test_podspec_container_and_image_failures_continue_with_siblings(self):
        self.write_candidate(
            "container-gaps.yaml",
            """apiVersion: v1
kind: List
items:
  - apiVersion: v1
    kind: Pod
    spec: scalar
  - apiVersion: v1
    kind: Pod
    spec: {}
  - apiVersion: v1
    kind: Pod
    spec: {containers: {image: invalid-array.example/app:1}}
  - apiVersion: v1
    kind: Pod
    spec:
      containers:
        - scalar-container
        - {name: missing-image}
        - {name: dynamic, image: "${IMAGE}"}
        - {name: invalid, image: "Bad Image"}
        - {name: valid, image: valid.example/app:1}
""",
        )
        self.commit()
        items = [
            item for item in self.kubernetes_observations(self.inventory())
            if item["source"]["path"].endswith("container-gaps.yaml")
        ]
        self.assertEqual(
            ["valid.example/app:1"],
            [item["value"]["image"] for item in items if item["kind"] == "container-image-reference"],
        )
        self.assertTrue(
            {
                "invalid-kubernetes-pod-spec",
                "missing-kubernetes-containers",
                "invalid-kubernetes-container-array",
                "invalid-kubernetes-container",
                "missing-kubernetes-image",
                "dynamic-kubernetes-image",
                "invalid-kubernetes-image",
            }
            <= {item["value"]["code"] for item in items if item["kind"] == "container-gap"}
        )
        serialized_values = json.dumps([item["value"] for item in items], sort_keys=True)
        self.assertNotIn("IMAGE", serialized_values)
        self.assertNotIn("Bad Image", serialized_values)

    def test_file_fatal_yaml_conditions_suppress_file_positives(self):
        cases = {
            "duplicate.yaml": (
                "apiVersion: v1\nkind: Pod\nkind: Pod\nspec: {containers: [{image: leaked.example/duplicate:1}]}\n",
                "duplicate-kubernetes-key",
            ),
            "key.yaml": (
                "? [complex, key]\n: value\napiVersion: v1\nkind: Pod\n",
                "unsupported-kubernetes-key",
            ),
            "tag.yaml": (
                "apiVersion: v1\nkind: Pod\nspec: {containers: [{image: !custom leaked.example/tag:1}]}\n",
                "unsupported-kubernetes-tag",
            ),
            "yaml-namespace-tag.yaml": (
                "apiVersion: v1\nkind: Pod\nspec: {containers: [{!!python/name:image image: leaked.example/yaml-tag:1}]}\n",
                "unsupported-kubernetes-tag",
            ),
            "anchor.yaml": (
                "apiVersion: v1\nkind: Pod\nspec: &pod {containers: [{image: leaked.example/anchor:1}]}\n",
                "unsupported-kubernetes-anchor",
            ),
            "alias.yaml": (
                "apiVersion: v1\nkind: Pod\nspec: *missing\n",
                "unsupported-kubernetes-alias",
            ),
            "merge.yaml": (
                "apiVersion: v1\nkind: Pod\nspec:\n  <<: {containers: [{image: leaked.example/merge:1}]}\n",
                "unsupported-kubernetes-merge",
            ),
            "malformed.yaml": (
                "apiVersion: v1\nkind: Pod\nspec: [unterminated\nSECRET-MARKER\n",
                "malformed-kubernetes-yaml",
            ),
        }
        for name, (content, _) in cases.items():
            self.write_candidate(name, content)
        self.commit()
        by_path = {}
        for item in self.kubernetes_observations(self.inventory()):
            by_path.setdefault(Path(item["source"]["path"]).name, []).append(item)
        for name, (_, code) in cases.items():
            with self.subTest(name=name):
                items = by_path[name]
                self.assertEqual(["container-gap"], [item["kind"] for item in items])
                self.assertEqual(code, items[0]["value"]["code"])
        serialized = json.dumps(by_path, sort_keys=True)
        self.assertNotIn("leaked.example", serialized)
        self.assertNotIn("SECRET-MARKER", serialized)

    def test_document_and_nested_list_limits_are_fixed_non_leaking_gaps(self):
        documents = "---\n".join(
            "apiVersion: v1\nkind: Service\nmetadata: {name: service-%d}\n" % index
            for index in range(65)
        )
        self.write_candidate("too-many-documents.yaml", documents)
        nested = "apiVersion: v1\nkind: List\nitems:\n"
        indent = "  "
        for _ in range(9):
            nested += indent + "- apiVersion: v1\n" + indent + "  kind: List\n" + indent + "  items:\n"
            indent += "    "
        nested += indent + "- apiVersion: v1\n" + indent + "  kind: Service\n"
        self.write_candidate("nested-lists.yaml", nested)
        self.commit()
        by_name = {}
        for item in self.gaps(self.inventory()):
            by_name.setdefault(Path(item["source"]["path"]).name, []).append(item)
        self.assertEqual(
            ["kubernetes-parser-resource-limit"],
            [item["value"]["code"] for item in by_name["too-many-documents.yaml"]],
        )
        self.assertIn(
            "kubernetes-parser-resource-limit",
            {item["value"]["code"] for item in by_name["nested-lists.yaml"]},
        )

    def test_depth_node_resource_and_container_limits_fail_closed(self):
        self.write_candidate(
            "too-deep.yaml",
            "value: " + "[" * 65 + "leaf" + "]" * 65 + "\n",
        )
        self.write_candidate(
            "too-many-nodes.yaml",
            "apiVersion: v1\nkind: Service\nmetadata:\n"
            + "".join("  key-%05d: value\n" % index for index in range(25_000)),
        )
        self.write_candidate(
            "too-many-resources.yaml",
            "apiVersion: v1\nkind: List\nitems:\n"
            + "".join(
                "  - {apiVersion: v1, kind: Service}\n" for _ in range(1_025)
            ),
        )
        self.write_candidate(
            "too-many-containers.yaml",
            "apiVersion: v1\nkind: Pod\nspec:\n  containers:\n"
            + "".join(
                "    - {image: limits.example/app:%d}\n" % index
                for index in range(4_097)
            ),
        )
        self.commit()
        by_name = {}
        for item in self.kubernetes_observations(self.inventory()):
            by_name.setdefault(Path(item["source"]["path"]).name, []).append(item)
        for name in (
            "too-deep.yaml",
            "too-many-nodes.yaml",
            "too-many-resources.yaml",
            "too-many-containers.yaml",
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    ["kubernetes-parser-resource-limit"],
                    [item["value"]["code"] for item in by_name[name]],
                )
                self.assertEqual(["container-gap"], [item["kind"] for item in by_name[name]])

    def test_existing_safety_filters_apply_before_yaml_parsing(self):
        secret = self.write_candidate(
            "deployment-secret.yaml",
            "apiVersion: v1\nkind: Pod\nsecret: DO-NOT-READ\n",
        )
        self.assertTrue(secret.exists())
        too_large = self.selection_root / "too-large.yaml"
        too_large.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        invalid_utf = self.selection_root / "invalid-utf.yml"
        invalid_utf.write_bytes(b"\xff\xfe\n")
        self.commit()
        inventory = self.inventory()
        excluded = {(item["path"], item["reason"]) for item in inventory["excluded"]}
        self.assertIn(
            (SELECTION_SUBPATH + "/deployment-secret.yaml", "potential-secret-file"),
            excluded,
        )
        self.assertIn((SELECTION_SUBPATH + "/too-large.yaml", "file-too-large"), excluded)
        self.assertIn(
            (SELECTION_SUBPATH + "/invalid-utf.yml", "unsupported-or-unreadable-content"),
            excluded,
        )
        self.assertNotIn("DO-NOT-READ", json.dumps(inventory, sort_keys=True))

    def test_selection_pair_validation_mismatch_and_unsafe_boundaries_fail_closed(self):
        with self.assertRaises(ValueError):
            discover(self.repository, REPOSITORY_ID, topology=self.topology)
        with self.assertRaises(ValueError):
            discover(self.repository, REPOSITORY_ID, selection=SELECTION_ID)

        mismatch = json.loads(json.dumps(self.topology))
        mismatch["sourceSelections"][0]["repositoryId"] = "different-repository"
        mismatch["repositories"].append({"id": "different-repository", "kind": "kubernetes"})
        mismatch["repositories"].sort(key=lambda item: item["id"])
        with self.assertRaises(ValueError):
            self.inventory(topology=mismatch)

        wrong_kind = json.loads(json.dumps(self.topology))
        wrong_kind["repositories"][0]["kind"] = "application"
        wrong_kind["sourceSelections"][0]["kind"] = "application"
        with self.assertRaises(ValueError):
            self.inventory(topology=wrong_kind)

        outside = json.loads(json.dumps(self.topology))
        outside["sourceSelections"][0]["subpath"] = "../outside"
        with self.assertRaises(ValueError):
            self.inventory(topology=outside)

        with self.assertRaises(ValueError):
            self.inventory(selection="missing-selection")

        inventory = self.inventory()
        malformed_selection = json.loads(json.dumps(inventory))
        malformed_selection["sourceSelection"] = []
        self.assertTrue(validate_inventory(malformed_selection))

        missing_selection = json.loads(json.dumps(inventory))
        del missing_selection["sourceSelection"]
        self.assertTrue(any(
            "requires an inventory sourceSelection" in error
            for error in validate_inventory(missing_selection)
        ))

        invalid_selection = json.loads(json.dumps(inventory))
        invalid_selection["sourceSelection"]["subpath"] = "unsafe//selection"
        self.assertTrue(validate_inventory(invalid_selection))

        outside_exclusion = json.loads(json.dumps(inventory))
        outside_exclusion["excluded"].append({
            "path": "outside.yaml", "reason": "test-outside-selection",
        })
        self.assertTrue(any(
            "outside sourceSelection" in error
            for error in validate_inventory(outside_exclusion)
        ))

    @unittest.skipUnless(hasattr(os, "symlink"), "symbolic links are unavailable")
    def test_selection_and_candidate_symlinks_never_escape_the_boundary(self):
        link = self.repository / "selected-link"
        link.symlink_to(self.selection_root, target_is_directory=True)
        self.commit("Add selection link")
        linked_topology = json.loads(json.dumps(self.topology))
        linked_topology["sourceSelections"][0]["subpath"] = "selected-link"
        with self.assertRaises(ValueError):
            self.inventory(topology=linked_topology)

        external = self.repository / "outside.yaml"
        external.write_text(
            "apiVersion: v1\nkind: Pod\nspec: {containers: [{image: escaped.example/app:1}]}\n",
            encoding="utf-8",
        )
        candidate_link = self.selection_root / "linked.yaml"
        candidate_link.symlink_to(external)
        self.commit("Add candidate file link")
        inventory = self.inventory()
        self.assertNotIn("escaped.example", json.dumps(inventory, sort_keys=True))
        self.assertIn(
            {"path": SELECTION_SUBPATH + "/linked.yaml", "reason": "symbolic-link"},
            inventory["excluded"],
        )

    def test_stable_cli_output_operational_validation_and_evidence_routing(self):
        self.write_candidate(
            "evidence-gap.yaml",
            "apiVersion: v1\nkind: Pod\nspec: {containers: [{image: \"${PRIVATE_IMAGE}\"}]}\n",
        )
        self.commit("Add evidence routing gap")
        command = [
            str(LANDSCAPE),
            "discover",
            str(self.repository),
            "--repository",
            REPOSITORY_ID,
            "--topology",
            str(self.topology_path),
            "--selection",
            SELECTION_ID,
        ]
        first = subprocess.run(command, check=False, capture_output=True, text=True)
        second = subprocess.run(command, check=False, capture_output=True, text=True)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual("", first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        inventory = json.loads(first.stdout)
        self.assertEqual([], validate_inventory(inventory, source=self.repository))

        bundle = select_evidence(inventory, self.repository)
        self.assertEqual("kubernetes", bundle["kind"])
        self.assertEqual(inventory["sourceSelection"], bundle["sourceSelection"])
        image_ids = {item["id"] for item in self.images(inventory)}
        selected_ids = {
            observation_id
            for item in bundle["selectedEvidence"]
            for observation_id in item["observationIds"]
        }
        gap_ids = {item["id"] for item in self.gaps(inventory)}
        bundled_gap_ids = {item["observationId"] for item in bundle["gaps"]}
        self.assertTrue(gap_ids)
        self.assertTrue(image_ids <= selected_ids)
        self.assertTrue(gap_ids <= bundled_gap_ids)
        self.assertTrue(gap_ids.isdisjoint(selected_ids))
        selected_content = "\n".join(item["content"] for item in bundle["selectedEvidence"])
        self.assertIn("registry.example.invalid/orders/api:1", selected_content)
        self.assertNotIn("scaleTargetRef", selected_content)

        outside_bundle = json.loads(json.dumps(bundle))
        outside_bundle["excluded"].append({
            "path": "outside.yaml", "reason": "test-outside-selection",
        })
        outside_bundle["excluded"].sort(
            key=lambda item: (item["path"], item["reason"])
        )
        self.assertTrue(any(
            "outside sourceSelection" in error
            for error in validate_evidence_bundle(outside_bundle)
        ))

        malformed_bundle = json.loads(json.dumps(bundle))
        malformed_bundle["sourceSelection"] = []
        self.assertTrue(validate_evidence_bundle(malformed_bundle))

        missing_bundle_selection = json.loads(json.dumps(bundle))
        del missing_bundle_selection["sourceSelection"]
        self.assertIn(
            "kind kubernetes requires sourceSelection",
            validate_evidence_bundle(missing_bundle_selection),
        )


if __name__ == "__main__":
    unittest.main()
