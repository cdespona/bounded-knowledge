"""Dependency-free validation for deterministic inventories."""

from pathlib import Path, PurePosixPath
import re

from .git_repository import commit_sha, require_repository_root
from .contracts import MANIFEST_KINDS, validate_manifest_observation
from .observations import observation
from .safety import exclusion_reason


REPOSITORY_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA = re.compile(r"^[a-f0-9]{40,64}$")
OBSERVATION_ID = re.compile(r"^[a-f0-9]{64}$")
TOP_LEVEL_FIELDS = {
    "schemaVersion",
    "repository",
    "commit",
    "detectors",
    "observations",
    "excluded",
}
OBSERVATION_FIELDS = {
    "id",
    "kind",
    "repository",
    "commit",
    "source",
    "detector",
    "value",
}


def _safe_relative_path(value):
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def validate_inventory(document, source=None):
    errors = []
    missing = (
        sorted(TOP_LEVEL_FIELDS - set(document))
        if isinstance(document, dict)
        else sorted(TOP_LEVEL_FIELDS)
    )
    if missing:
        return ["Missing top-level fields: {}".format(", ".join(missing))]
    unexpected = sorted(set(document) - TOP_LEVEL_FIELDS)
    if unexpected:
        errors.append("Unexpected top-level fields: {}".format(", ".join(unexpected)))

    if document["schemaVersion"] != 1:
        errors.append("schemaVersion must be 1")
    if not isinstance(document["repository"], str) or not REPOSITORY_ID.fullmatch(
        document["repository"]
    ):
        errors.append("repository must be a lowercase kebab-case identifier")
    if not isinstance(document["commit"], str) or not SHA.fullmatch(document["commit"]):
        errors.append("commit must be a full hexadecimal Git SHA")
    if not isinstance(document["detectors"], list):
        errors.append("detectors must be an array")
    else:
        for index, detector in enumerate(document["detectors"]):
            if (
                not isinstance(detector, dict)
                or set(detector) != {"name", "version"}
                or not isinstance(detector.get("name"), str)
                or not detector.get("name")
                or not isinstance(detector.get("version"), int)
                or detector.get("version", 0) < 1
            ):
                errors.append("detectors[{}] is invalid".format(index))
    if not isinstance(document["observations"], list):
        errors.append("observations must be an array")
        return errors

    seen = set()
    for index, item in enumerate(document["observations"]):
        prefix = "observations[{}]".format(index)
        if not isinstance(item, dict):
            errors.append("{} must be an object".format(prefix))
            continue
        unexpected_item_fields = sorted(set(item) - OBSERVATION_FIELDS)
        if unexpected_item_fields:
            errors.append(
                "{} has unexpected fields: {}".format(
                    prefix, ", ".join(unexpected_item_fields)
                )
            )
        for field in ("id", "kind", "repository", "commit", "detector", "value"):
            if field not in item:
                errors.append("{} is missing {}".format(prefix, field))
        item_id = item.get("id")
        if not isinstance(item_id, str) or not OBSERVATION_ID.fullmatch(item_id):
            errors.append("{}.id must be a SHA-256 hexadecimal value".format(prefix))
        elif item_id in seen:
            errors.append("{}.id is duplicated".format(prefix))
        else:
            seen.add(item_id)
        if item.get("repository") != document["repository"]:
            errors.append("{}.repository does not match the inventory".format(prefix))
        if item.get("commit") != document["commit"]:
            errors.append("{}.commit does not match the inventory".format(prefix))
        detector = item.get("detector")
        detector_valid = (
            isinstance(detector, dict)
            and set(detector) == {"name", "version"}
            and isinstance(detector.get("name"), str)
            and bool(detector.get("name"))
            and isinstance(detector.get("version"), int)
            and detector.get("version", 0) >= 1
        )
        if not detector_valid:
            errors.append("{}.detector is invalid".format(prefix))
        source_ref = item.get("source")
        if source_ref is not None:
            if not isinstance(source_ref, dict) or not _safe_relative_path(source_ref.get("path", "")):
                errors.append("{}.source.path must be a safe relative path".format(prefix))
            elif exclusion_reason(Path(source_ref["path"])) is not None:
                errors.append("{}.source.path references an excluded path".format(prefix))

        if detector_valid and all(field in item for field in ("kind", "repository", "commit", "value")):
            expected = observation(
                kind=item["kind"],
                repository=item["repository"],
                commit=item["commit"],
                detector=detector["name"],
                detector_version=detector["version"],
                value=item["value"],
                source_path=source_ref.get("path") if isinstance(source_ref, dict) else None,
                source_lines=source_ref.get("lines") if isinstance(source_ref, dict) else None,
            )["id"]
            if item_id != expected:
                errors.append("{}.id does not match its content".format(prefix))
        if item.get("kind") in MANIFEST_KINDS:
            errors.extend(validate_manifest_observation(item, prefix))

    if not isinstance(document["excluded"], list):
        errors.append("excluded must be an array")
    else:
        for index, item in enumerate(document["excluded"]):
            if (
                not isinstance(item, dict)
                or set(item) != {"path", "reason"}
                or not _safe_relative_path(item.get("path", ""))
                or not isinstance(item.get("reason"), str)
                or not item.get("reason")
            ):
                errors.append("excluded[{}] is invalid".format(index))

    if source is not None:
        try:
            root = require_repository_root(Path(source))
            if commit_sha(root) != document["commit"]:
                errors.append("Inventory commit does not match the source repository HEAD")
            for item in document["observations"]:
                if not isinstance(item, dict):
                    continue
                source_ref = item.get("source")
                if source_ref and _safe_relative_path(source_ref.get("path", "")):
                    if not (root / source_ref["path"]).is_file():
                        errors.append("Evidence path does not exist: {}".format(source_ref["path"]))
        except ValueError as error:
            errors.append(str(error))
    return errors
