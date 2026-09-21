"""Focused tests for the bounded Dockerfile container-image inventory."""

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
from landscape_core.observations import observation  # noqa: E402
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


class ContainerImageDetectorTest(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.base = Path(self.temporary_directory.name)

    def make_repository(self, fixture="synthetic-java-service"):
        target = self.base / fixture
        shutil.copytree(str(PROJECT / "examples" / fixture), str(target))
        git(target, "init", "-q")
        git(target, "config", "user.name", "Mercurio Test")
        git(target, "config", "user.email", "mercurio@example.invalid")
        git(target, "add", ".")
        git(target, "commit", "-q", "-m", "Synthetic fixture")
        return target

    def commit(self, repository, message="Add container inventory test case"):
        git(repository, "add", ".")
        git(repository, "commit", "-q", "-m", message)

    @staticmethod
    def container_observations(inventory):
        return [
            item
            for item in inventory["observations"]
            if item["detector"]["name"] == "container-images"
        ]

    def test_versioned_fixtures_cover_external_scratch_and_stage_references(self):
        java_repository = self.make_repository()
        java_inventory = discover(java_repository, "synthetic-java-service")
        self.assertEqual([], validate_inventory(java_inventory, source=java_repository))
        java = self.container_observations(java_inventory)
        self.assertEqual(
            {
                (
                    "container-image-reference",
                    "base-image",
                    "dockerfile-from",
                    "eclipse-temurin:21-jre",
                    "runtime",
                    "1",
                ),
                (
                    "container-stage-reference",
                    "build-stage-base",
                    "dockerfile-from-stage",
                    "runtime",
                    "final",
                    "2",
                ),
            },
            {
                (
                    item["kind"],
                    item["value"]["role"],
                    item["value"]["form"],
                    item["value"].get("image", item["value"].get("stage")),
                    item["value"].get("stageAlias"),
                    item["source"]["lines"],
                )
                for item in java
                if item["kind"] != "container-gap"
            },
        )

        kotlin_repository = self.make_repository("synthetic-kotlin-service")
        kotlin_inventory = discover(kotlin_repository, "synthetic-kotlin-service")
        self.assertEqual([], validate_inventory(kotlin_inventory, source=kotlin_repository))
        kotlin = self.container_observations(kotlin_inventory)
        self.assertTrue(any(
            item["kind"] == "container-image-reference"
            and item["value"]["role"] == "scratch-base"
            and item["value"]["image"] == "scratch"
            and item["value"]["stageAlias"] == "seed"
            and item["source"]["lines"] == "1"
            for item in kotlin
        ))
        self.assertTrue(any(
            item["kind"] == "container-image-reference"
            and item["value"]["image"].startswith(
                "registry.example.invalid:5000/mercurio/kotlin-service:1.0@sha256:"
            )
            and item["value"]["stageAlias"] == "runtime"
            and item["source"]["lines"] == "2"
            for item in kotlin
        ))
        self.assertTrue(any(
            item["kind"] == "container-stage-reference"
            and item["value"]["stage"] == "runtime"
            and "stageAlias" not in item["value"]
            and item["source"]["lines"] == "3"
            for item in kotlin
        ))

    def test_stage_classification_is_ordered_exact_and_ambiguity_is_visible(self):
        repository = self.make_repository()
        path = repository / "stages.Dockerfile"
        path.write_text(
            "FROM alpine:3.20 AS Build\n"
            "FROM Build AS exact\n"
            "FROM build\n"
            "FROM future AS forward\n"
            "FROM busybox:1 AS future\n"
            "FROM busybox:1 AS Build\n"
            "FROM Build\n"
            "FROM --platform=linux/amd64 alpine:3.20 AS platformed\n"
            "FROM platformed\n",
            encoding="utf-8",
        )
        self.commit(repository)

        observations = [
            item for item in self.container_observations(
                discover(repository, "synthetic-java-service")
            )
            if item["source"]["path"] == "stages.Dockerfile"
        ]
        references = {
            (item["kind"], item["source"]["lines"]): item["value"]
            for item in observations if item["kind"] != "container-gap"
        }
        self.assertEqual("Build", references[("container-stage-reference", "2")]["stage"])
        self.assertEqual("future", references[("container-image-reference", "4")]["image"])
        gaps = {
            (item["source"]["lines"], item["value"]["code"])
            for item in observations if item["kind"] == "container-gap"
        }
        self.assertTrue({
            ("3", "ambiguous-stage-reference"),
            ("6", "duplicate-stage-alias"),
            ("7", "ambiguous-stage-reference"),
            ("8", "unsupported-from-option"),
            ("9", "unsupported-stage-source"),
        } <= gaps, gaps)

    def test_dynamic_template_malformed_and_invalid_forms_are_gaps(self):
        repository = self.make_repository()
        path = repository / "Dockerfile.gaps"
        path.write_text(
            "FROM $BASE\n"
            "FROM repo:${TAG}\n"
            "FROM {{image}}\n"
            "FROM --platform=$BUILDPLATFORM alpine:3.20\n"
            "FROM\n"
            "FROM \"alpine:3.20\"\n"
            "FROM alpine:3.20 trailing\n"
            "FROM alpine:3.20 # comment\n"
            "FROM Alpine:3.20\n"
            "FROM alpine:3.20 AS bad/alias\n",
            encoding="utf-8",
        )
        self.commit(repository)

        gaps = [
            item for item in self.container_observations(
                discover(repository, "synthetic-java-service")
            )
            if item["source"]["path"] == "Dockerfile.gaps"
            and item["kind"] == "container-gap"
        ]
        codes = [item["value"]["code"] for item in gaps]
        self.assertTrue({
            "dynamic-image-reference",
            "templated-image-reference",
            "unsupported-from-option",
            "malformed-from",
            "invalid-image-reference",
            "invalid-stage-alias",
        } <= set(codes), codes)
        self.assertTrue(all(
            item["value"]["context"] == "dockerfile-from"
            and item["value"]["form"] == "dockerfile-from"
            and item["value"]["detail"]
            for item in gaps
        ))
        serialized = json.dumps([item["value"] for item in gaps], sort_keys=True)
        for source_value in ("BASE", "TAG", "BUILDPLATFORM", "bad/alias"):
            self.assertNotIn(source_value, serialized)

    def test_continuation_and_heredoc_text_cannot_create_from_references(self):
        repository = self.make_repository()
        continuation = repository / "continued.Dockerfile"
        continuation.write_text(
            "RUN printf hello \\\n"
            "# ignored continuation comment\n"
            "FROM false-positive\n"
            "FROM alpine:3.20 AS valid\n",
            encoding="utf-8",
        )
        heredoc = repository / "Dockerfile.heredoc"
        heredoc.write_text(
            "RUN <<EOF\n"
            "FROM false-positive\n"
            "EOF\n"
            "FROM alpine:3.20\n",
            encoding="utf-8",
        )
        quoted_heredoc = repository / "quoted-heredoc.Dockerfile"
        quoted_heredoc.write_text(
            "RUN <<'EOF'\n"
            "FROM quoted-false-positive\n"
            "EOF\n"
            "FROM busybox:1\n",
            encoding="utf-8",
        )
        escape = repository / "Dockerfile.escape"
        escape.write_text(
            "# escape=`\n"
            "FROM alpine:3.20\n",
            encoding="utf-8",
        )
        self.commit(repository)

        observations = self.container_observations(
            discover(repository, "synthetic-java-service")
        )
        by_path = {}
        for item in observations:
            by_path.setdefault(item["source"]["path"], []).append(item)

        continued = by_path["continued.Dockerfile"]
        self.assertIn(
            "unsupported-from-continuation",
            {item["value"]["code"] for item in continued if item["kind"] == "container-gap"},
        )
        self.assertEqual(
            ["alpine:3.20"],
            [
                item["value"]["image"] for item in continued
                if item["kind"] == "container-image-reference"
            ],
        )
        self.assertNotIn("false-positive", json.dumps(continued, sort_keys=True))

        for candidate, code in (
            ("Dockerfile.heredoc", "unsupported-heredoc"),
            ("quoted-heredoc.Dockerfile", "unsupported-heredoc"),
            ("Dockerfile.escape", "unsupported-escape-directive"),
        ):
            items = by_path[candidate]
            self.assertEqual(
                [code],
                [item["value"]["code"] for item in items if item["kind"] == "container-gap"],
            )
            self.assertFalse(any(item["kind"] != "container-gap" for item in items))

    def test_ordinary_comments_do_not_change_parser_state(self):
        repository = self.make_repository()
        path = repository / "comments.Dockerfile"
        path.write_text(
            "# ordinary comment \\\n"
            "FROM alpine:3.20\n"
            "# escape=`\n"
            "FROM busybox:1\n",
            encoding="utf-8",
        )
        self.commit(repository)
        observations = [
            item for item in self.container_observations(
                discover(repository, "synthetic-java-service")
            )
            if item["source"]["path"] == "comments.Dockerfile"
        ]
        self.assertEqual(
            {"alpine:3.20", "busybox:1"},
            {
                item["value"]["image"] for item in observations
                if item["kind"] == "container-image-reference"
            },
        )
        self.assertFalse(any(item["kind"] == "container-gap" for item in observations))

    def test_candidate_and_false_positive_boundaries_remain_silent(self):
        repository = self.make_repository()
        candidates = {
            "Dockerfile.dev": "FROM alpine:3.20 AS dev\n",
            "service.Dockerfile": "from scratch as empty\n",
        }
        non_candidates = {
            "dockerfile": "FROM ignored-lowercase\n",
            "DOCKERFILE": "FROM ignored-uppercase\n",
            ".Dockerfile": "FROM ignored-empty-prefix\n",
            "Dockerfile.": "FROM ignored-empty-suffix\n",
            "myDockerfile": "FROM ignored-no-separator\n",
            "Dockerfile.bak.txt": "FROM ignored-extra-suffix\n",
            "deployment.yaml": "image: ignored-yaml\n",
            "deployment.json": '{"image":"ignored-json"}\n',
            "README-container.md": "FROM ignored-markdown\n",
        }
        expected_candidate_paths = set()
        expected_non_candidate_paths = set()
        for index, (name, content) in enumerate(
            {**candidates, **non_candidates}.items()
        ):
            directory = repository / "candidate-boundary" / str(index)
            directory.mkdir(parents=True)
            (directory / name).write_text(content, encoding="utf-8")
            relative = (directory / name).relative_to(repository).as_posix()
            if name in candidates:
                expected_candidate_paths.add(relative)
            else:
                expected_non_candidate_paths.add(relative)
        lookalikes = repository / "lookalikes.Dockerfile"
        lookalikes.write_text(
            "# FROM ignored-comment\n"
            "RUN echo FROM ignored-run\n"
            "ENV NOTE=FROM\n"
            "FROMAGE ignored-prefix\n",
            encoding="utf-8",
        )
        self.commit(repository)

        observations = self.container_observations(
            discover(repository, "synthetic-java-service")
        )
        paths = {item["source"]["path"] for item in observations}
        self.assertTrue(expected_candidate_paths <= paths)
        self.assertTrue(expected_non_candidate_paths.isdisjoint(paths))
        serialized = json.dumps(observations, sort_keys=True)
        for marker in (
            "ignored-lowercase", "ignored-uppercase", "ignored-empty-prefix",
            "ignored-empty-suffix", "ignored-no-separator", "ignored-extra-suffix",
            "ignored-yaml", "ignored-json", "ignored-markdown", "ignored-comment",
            "ignored-run", "ignored-prefix",
        ):
            self.assertNotIn(marker, serialized)

    def test_candidate_safety_exclusions_and_skips(self):
        repository = self.make_repository()
        binary = repository / "binary.Dockerfile"
        binary.write_bytes(b"FROM alpine:3.20\xff")
        oversized = repository / "Dockerfile.large"
        oversized.write_bytes(b"FROM " + b"a" * MAX_FILE_BYTES)
        linked = repository / "linked.Dockerfile"
        linked.symlink_to("Dockerfile")
        sensitive = repository / "secret.Dockerfile"
        sensitive.write_text("FROM secret-image\n", encoding="utf-8")
        generated = repository / "build" / "generated.Dockerfile"
        generated.parent.mkdir()
        generated.write_text("FROM generated-image\n", encoding="utf-8")
        self.commit(repository)

        inventory = discover(repository, "synthetic-java-service")
        paths = {
            item["source"]["path"] for item in self.container_observations(inventory)
        }
        blocked = {
            "binary.Dockerfile", "Dockerfile.large", "linked.Dockerfile",
            "secret.Dockerfile", "build/generated.Dockerfile",
        }
        self.assertTrue(blocked.isdisjoint(paths))
        self.assertIn(
            {"path": "binary.Dockerfile", "reason": "unsupported-or-unreadable-content"},
            inventory["excluded"],
        )
        self.assertIn(
            {"path": "Dockerfile.large", "reason": "file-too-large"},
            inventory["excluded"],
        )
        self.assertIn(
            {"path": "secret.Dockerfile", "reason": "potential-secret-file"},
            inventory["excluded"],
        )

    def test_discovery_is_stable_registered_and_operationally_valid(self):
        repository = self.make_repository()
        first = discover(repository, "synthetic-java-service")
        second = discover(repository, "synthetic-java-service")
        self.assertEqual(first, second)
        self.assertEqual(
            json.dumps(first, sort_keys=True, separators=(",", ":")),
            json.dumps(second, sort_keys=True, separators=(",", ":")),
        )
        self.assertIn({"name": "container-images", "version": 1}, first["detectors"])
        self.assertEqual([], validate_inventory(first, source=repository))

    def test_image_stage_alias_path_and_line_participate_in_identity(self):
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
            "image": "alpine:3.20",
            "stageAlias": "build",
        }
        first = observation(source_lines="1", value=value, **common)
        moved = observation(source_lines="2", value=value, **common)
        changed_image = observation(
            source_lines="1", value=dict(value, image="alpine:3.21"), **common
        )
        changed_alias = observation(
            source_lines="1", value=dict(value, stageAlias="runtime"), **common
        )
        changed_path = observation(
            source_lines="1", value=value, **dict(common, source_path="service.Dockerfile")
        )
        self.assertEqual(5, len({
            first["id"], moved["id"], changed_image["id"],
            changed_alias["id"], changed_path["id"],
        }))

    def test_references_and_gaps_route_to_bounded_evidence(self):
        repository = self.make_repository()
        path = repository / "Dockerfile.evidence"
        path.write_text(
            "FROM alpine:3.20 AS build\n"
            "FROM build\n"
            "FROM ${RUNTIME_IMAGE}\n",
            encoding="utf-8",
        )
        self.commit(repository)

        inventory = discover(repository, "synthetic-java-service")
        observations = [
            item for item in self.container_observations(inventory)
            if item["source"]["path"] == "Dockerfile.evidence"
        ]
        reference_ids = {
            item["id"] for item in observations if item["kind"] != "container-gap"
        }
        gap_ids = {
            item["id"] for item in observations if item["kind"] == "container-gap"
        }
        bundle = select_evidence(inventory, repository)
        selected_ids = {
            observation_id
            for item in bundle["selectedEvidence"]
            for observation_id in item["observationIds"]
        }
        bundled_gap_ids = {item["observationId"] for item in bundle["gaps"]}
        self.assertTrue(reference_ids <= selected_ids)
        self.assertTrue(gap_ids <= bundled_gap_ids)
        self.assertTrue(gap_ids.isdisjoint(selected_ids))
        selected_lines = {
            item["lines"] for item in bundle["selectedEvidence"]
            if item["path"] == "Dockerfile.evidence"
        }
        self.assertEqual({"1", "2"}, selected_lines)


if __name__ == "__main__":
    unittest.main()
