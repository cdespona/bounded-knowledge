"""Dependency-free validation for persisted Slice 1 contracts."""

import hashlib
import json
from datetime import datetime
from pathlib import Path, PurePosixPath
import re


REPOSITORY_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SHA = re.compile(r"^[a-f0-9]{40,64}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
LINES = re.compile(r"^[1-9][0-9]*(-[1-9][0-9]*)?$")
KINDS = {"application", "kubernetes", "terraform", "pipeline", "other"}
BUILD_SYSTEMS = {"maven", "gradle"}
DECLARATIONS = {"literal", "property-reference", "dynamic"}
MANIFEST_KINDS = {
    "build-project",
    "declared-dependency",
    "build-plugin",
    "build-module",
    "manifest-gap",
}


def artifact_id(document):
    """Return the stable SHA-256 identity of a JSON artifact without its id field."""
    content = dict(document)
    content.pop("id", None)
    encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_non_empty_string(value):
    return isinstance(value, str) and bool(value)


def _is_safe_relative_path(value):
    if not _is_non_empty_string(value):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and ".." not in path.parts


def _exact_fields(value, required, optional=()):
    if not isinstance(value, dict):
        return False
    fields = set(value)
    return set(required) <= fields <= set(required) | set(optional)


def _valid_lines(value):
    if not isinstance(value, str) or not LINES.fullmatch(value):
        return False
    parts = value.split("-", 1)
    return len(parts) == 1 or int(parts[0]) <= int(parts[1])


def _is_datetime(value):
    if not isinstance(value, str):
        return False
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_source_reference(value, prefix="source", require_lines=False):
    errors = []
    required = {"path", "lines"} if require_lines else {"path"}
    if not _exact_fields(value, required, {"lines"} - required):
        suffix = " and lines" if require_lines else ""
        return ["{} must contain only path{}".format(prefix, suffix)]
    if not _is_safe_relative_path(value["path"]):
        errors.append("{}.path must be a safe relative path".format(prefix))
    if "lines" in value and not _valid_lines(value["lines"]):
        errors.append(
            "{}.lines must be a non-descending positive line or line range".format(prefix)
        )
    return errors


def validate_manifest_observation(item, prefix="observation"):
    """Validate the kind-specific contract for a parsed manifest observation."""
    errors = []
    if not isinstance(item, dict):
        return ["{} must be an object".format(prefix)]
    kind = item.get("kind")
    if not isinstance(kind, str) or kind not in MANIFEST_KINDS:
        return ["{}.kind is not a parsed manifest observation kind".format(prefix)]
    errors.extend(validate_source_reference(item.get("source"), prefix + ".source", True))
    value = item.get("value")
    if not isinstance(value, dict):
        return errors + ["{}.value must be an object".format(prefix)]
    if (
        not isinstance(value.get("buildSystem"), str)
        or value.get("buildSystem") not in BUILD_SYSTEMS
    ):
        errors.append("{}.value.buildSystem must be maven or gradle".format(prefix))

    if kind == "build-project":
        if not _exact_fields(
            value, {"buildSystem", "artifact"}, {"group", "version", "packaging"}
        ):
            errors.append("{}.value has invalid build-project fields".format(prefix))
        for field in set(value) - {"buildSystem"}:
            if not _is_non_empty_string(value[field]):
                errors.append("{}.value.{} must be a non-empty string".format(prefix, field))
    elif kind == "declared-dependency":
        required = {"buildSystem", "group", "artifact", "scope", "declaration"}
        if not _exact_fields(value, required, {"version"}):
            errors.append("{}.value has invalid declared-dependency fields".format(prefix))
        for field in required - {"buildSystem", "declaration"}:
            if not _is_non_empty_string(value.get(field)):
                errors.append("{}.value.{} must be a non-empty string".format(prefix, field))
        if (
            not isinstance(value.get("declaration"), str)
            or value.get("declaration") not in DECLARATIONS
        ):
            errors.append("{}.value.declaration is invalid".format(prefix))
        if "version" in value and not _is_non_empty_string(value["version"]):
            errors.append("{}.value.version must be a non-empty string".format(prefix))
    elif kind == "build-plugin":
        required = {"buildSystem", "plugin", "declaration"}
        if not _exact_fields(value, required, {"version"}):
            errors.append("{}.value has invalid build-plugin fields".format(prefix))
        if not _is_non_empty_string(value.get("plugin")):
            errors.append("{}.value.plugin must be a non-empty string".format(prefix))
        if (
            not isinstance(value.get("declaration"), str)
            or value.get("declaration") not in DECLARATIONS
        ):
            errors.append("{}.value.declaration is invalid".format(prefix))
        if "version" in value and not _is_non_empty_string(value["version"]):
            errors.append("{}.value.version must be a non-empty string".format(prefix))
    elif kind == "build-module":
        if not _exact_fields(value, {"buildSystem", "path"}):
            errors.append("{}.value has invalid build-module fields".format(prefix))
        elif not _is_safe_relative_path(value["path"]):
            errors.append("{}.value.path must be a safe relative path".format(prefix))
    elif kind == "manifest-gap":
        if not _exact_fields(value, {"buildSystem", "code", "detail"}):
            errors.append("{}.value has invalid manifest-gap fields".format(prefix))
        for field in ("code", "detail"):
            if not _is_non_empty_string(value.get(field)):
                errors.append("{}.value.{} must be a non-empty string".format(prefix, field))
    return errors


def validate_source_registry(document):
    errors = []
    if not _exact_fields(document, {"schemaVersion", "repositories"}):
        return ["registry must contain only schemaVersion and repositories"]
    if document["schemaVersion"] != 1:
        errors.append("schemaVersion must be 1")
    if not isinstance(document["repositories"], list):
        return errors + ["repositories must be an array"]
    identifiers = set()
    paths = set()
    for index, item in enumerate(document["repositories"]):
        prefix = "repositories[{}]".format(index)
        required = {"id", "kind", "path", "enabled"}
        optional = {"approvedCopilotConfigurationSha256", "exclude"}
        if not _exact_fields(item, required, optional):
            errors.append("{} has invalid fields".format(prefix))
            continue
        if not isinstance(item["id"], str) or not REPOSITORY_ID.fullmatch(item["id"]):
            errors.append("{}.id must be lowercase kebab-case".format(prefix))
        elif item["id"] in identifiers:
            errors.append("{}.id is duplicated".format(prefix))
        if isinstance(item["id"], str):
            identifiers.add(item["id"])
        if not isinstance(item["kind"], str) or item["kind"] not in KINDS:
            errors.append("{}.kind is invalid".format(prefix))
        if (
            not isinstance(item["path"], str)
            or not Path(item["path"]).is_absolute()
            or ".." in Path(item["path"]).parts
        ):
            errors.append(
                "{}.path must be absolute and contain no '..' component".format(prefix)
            )
        elif item["path"] in paths:
            errors.append("{}.path is duplicated".format(prefix))
        if isinstance(item["path"], str):
            paths.add(item["path"])
        if not isinstance(item["enabled"], bool):
            errors.append("{}.enabled must be boolean".format(prefix))
        digest = item.get("approvedCopilotConfigurationSha256")
        if digest is not None and (not isinstance(digest, str) or not SHA256.fullmatch(digest)):
            errors.append("{}.approvedCopilotConfigurationSha256 is invalid".format(prefix))
        exclusions = item.get("exclude", [])
        if (
            not isinstance(exclusions, list)
            or any(not isinstance(value, str) for value in exclusions)
            or len(exclusions) != len(set(exclusions))
        ):
            errors.append("{}.exclude must contain unique paths".format(prefix))
        else:
            if exclusions != sorted(exclusions):
                errors.append("{}.exclude must be sorted".format(prefix))
            for exclusion in exclusions:
                if not _is_safe_relative_path(exclusion):
                    errors.append("{}.exclude contains an unsafe path".format(prefix))
    repository_order = [
        item.get("id") for item in document["repositories"] if isinstance(item, dict)
    ]
    if all(isinstance(value, str) for value in repository_order) and (
        repository_order != sorted(repository_order)
    ):
        errors.append("repositories must be sorted by id")
    return errors


def validate_source_resolution(document):
    required = {
        "schemaVersion", "repository", "kind", "configuredPath", "resolvedPath",
        "commit", "exclusions", "copilotConfigurationFiles", "copilotConfigurationSha256",
        "copilotAccessApproved",
    }
    if not _exact_fields(document, required):
        return ["source resolution has invalid fields"]
    errors = []
    if document["schemaVersion"] != 1:
        errors.append("schemaVersion must be 1")
    if not isinstance(document["repository"], str) or not REPOSITORY_ID.fullmatch(
        document["repository"]
    ):
        errors.append("repository must be lowercase kebab-case")
    if not isinstance(document["kind"], str) or document["kind"] not in KINDS:
        errors.append("kind is invalid")
    for field in ("configuredPath", "resolvedPath"):
        if not isinstance(document[field], str) or not Path(document[field]).is_absolute():
            errors.append("{} must be absolute".format(field))
    if not isinstance(document["commit"], str) or not SHA.fullmatch(document["commit"]):
        errors.append("commit must be a full hexadecimal Git SHA")
    for field in ("exclusions", "copilotConfigurationFiles"):
        values = document[field]
        if (
            not isinstance(values, list)
            or any(not isinstance(value, str) for value in values)
            or values != sorted(set(values))
        ):
            errors.append("{} must be a sorted unique array".format(field))
        elif any(not _is_safe_relative_path(value) for value in values):
            errors.append("{} contains an unsafe path".format(field))
    digest = document["copilotConfigurationSha256"]
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        errors.append("copilotConfigurationSha256 is invalid")
    if not isinstance(document["copilotAccessApproved"], bool):
        errors.append("copilotAccessApproved must be boolean")
    return errors


def validate_evidence_bundle(document):
    required = {
        "schemaVersion", "id", "repository", "kind", "commit", "inventorySha256",
        "selectedEvidence", "gaps", "excluded",
    }
    if not _exact_fields(document, required):
        return ["evidence bundle has invalid fields"]
    errors = []
    if document["schemaVersion"] != 1:
        errors.append("schemaVersion must be 1")
    if not isinstance(document["id"], str) or document["id"] != artifact_id(document):
        errors.append("id does not match the evidence bundle content")
    if not isinstance(document["repository"], str) or not REPOSITORY_ID.fullmatch(
        document["repository"]
    ):
        errors.append("repository must be lowercase kebab-case")
    if not isinstance(document["kind"], str) or document["kind"] not in KINDS:
        errors.append("kind is invalid")
    if not isinstance(document["commit"], str) or not SHA.fullmatch(document["commit"]):
        errors.append("commit must be a full hexadecimal Git SHA")
    if not isinstance(document["inventorySha256"], str) or not SHA256.fullmatch(
        document["inventorySha256"]
    ):
        errors.append("inventorySha256 is invalid")

    evidence = document["selectedEvidence"]
    if not isinstance(evidence, list):
        errors.append("selectedEvidence must be an array")
    else:
        ordering = []
        for index, item in enumerate(evidence):
            prefix = "selectedEvidence[{}]".format(index)
            required_item = {"path", "lines", "content", "contentSha256", "observationIds"}
            if not _exact_fields(item, required_item):
                errors.append("{} has invalid fields".format(prefix))
                continue
            if isinstance(item["path"], str) and isinstance(item["lines"], str):
                ordering.append((item["path"], item["lines"]))
            errors.extend(validate_source_reference(
                {"path": item["path"], "lines": item["lines"]}, prefix, True
            ))
            content = item["content"]
            digest = (
                hashlib.sha256(content.encode("utf-8")).hexdigest()
                if isinstance(content, str)
                else None
            )
            if digest != item["contentSha256"]:
                errors.append("{}.contentSha256 does not match content".format(prefix))
            observation_ids = item["observationIds"]
            if (
                not isinstance(observation_ids, list)
                or not observation_ids
                or any(not isinstance(value, str) for value in observation_ids)
                or observation_ids != sorted(set(observation_ids))
                or any(
                    not SHA256.fullmatch(value)
                    for value in observation_ids
                    if isinstance(value, str)
                )
            ):
                errors.append(
                    "{}.observationIds must be a sorted unique non-empty array".format(prefix)
                )
        if ordering != sorted(ordering):
            errors.append("selectedEvidence must be sorted by path and lines")

    gaps = document["gaps"]
    if not isinstance(gaps, list):
        errors.append("gaps must be an array")
    else:
        gap_order = []
        for index, item in enumerate(gaps):
            prefix = "gaps[{}]".format(index)
            if not _exact_fields(item, {"code", "detail", "source"}, {"observationId"}):
                errors.append("{} has invalid fields".format(prefix))
                continue
            if not _is_non_empty_string(item["code"]) or not _is_non_empty_string(item["detail"]):
                errors.append("{} code and detail must be non-empty".format(prefix))
            errors.extend(validate_source_reference(item["source"], prefix + ".source", True))
            source = item["source"]
            if (
                isinstance(source, dict)
                and isinstance(source.get("path"), str)
                and isinstance(source.get("lines"), str)
            ):
                gap_order.append((source["path"], source["lines"], item["code"]))
            if "observationId" in item and (
                not isinstance(item["observationId"], str)
                or not SHA256.fullmatch(item["observationId"])
            ):
                errors.append("{}.observationId is invalid".format(prefix))
        if gap_order != sorted(gap_order):
            errors.append("gaps must be sorted by source path, lines, and code")

    excluded = document["excluded"]
    if not isinstance(excluded, list):
        errors.append("excluded must be an array")
    else:
        excluded_order = []
        for index, item in enumerate(excluded):
            if (
                not _exact_fields(item, {"path", "reason"})
                or not _is_safe_relative_path(item.get("path"))
                or not _is_non_empty_string(item.get("reason"))
            ):
                errors.append("excluded[{}] is invalid".format(index))
            elif isinstance(item["path"], str) and isinstance(item["reason"], str):
                excluded_order.append((item["path"], item["reason"]))
        if excluded_order != sorted(excluded_order):
            errors.append("excluded must be sorted by path and reason")
    return errors


def validate_candidate_envelope(document, evidence_bundle=None):
    required = {
        "schemaVersion", "repository", "kind", "analyzedCommit", "evidenceBundleId",
        "proposedProfile",
    }
    if not _exact_fields(document, required):
        return ["candidate envelope has invalid fields"]
    errors = []
    if document["schemaVersion"] != 1:
        errors.append("schemaVersion must be 1")
    repository = document["repository"]
    commit = document["analyzedCommit"]
    kind = document["kind"]
    if not isinstance(repository, str) or not REPOSITORY_ID.fullmatch(repository):
        errors.append("repository must be lowercase kebab-case")
    if not isinstance(kind, str) or kind not in KINDS:
        errors.append("kind is invalid")
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        errors.append("analyzedCommit must be a full hexadecimal Git SHA")
    if not isinstance(document["evidenceBundleId"], str) or not SHA256.fullmatch(
        document["evidenceBundleId"]
    ):
        errors.append("evidenceBundleId is invalid")
    profile = document["proposedProfile"]
    profile_required = {
        "schemaVersion", "repository", "kind", "analyzedCommit", "claims", "openQuestions"
    }
    if not _exact_fields(profile, profile_required):
        return errors + ["proposedProfile has invalid fields"]
    if profile["schemaVersion"] != 1:
        errors.append("proposedProfile.schemaVersion must be 1")
    for field, expected in (
        ("repository", repository), ("kind", kind), ("analyzedCommit", commit)
    ):
        if profile[field] != expected:
            errors.append("proposedProfile.{} does not match the envelope".format(field))
    if not isinstance(profile["openQuestions"], list) or any(
        not _is_non_empty_string(value) for value in profile["openQuestions"]
    ):
        errors.append("proposedProfile.openQuestions must be an array of non-empty strings")
    claims = profile["claims"]
    if not isinstance(claims, list):
        errors.append("proposedProfile.claims must be an array")
        claims = []
    claim_ids = set()
    for index, claim in enumerate(claims):
        prefix = "proposedProfile.claims[{}]".format(index)
        required_claim = {
            "id", "statement", "status", "confidence", "evidence", "counterevidence",
            "analyzedAt",
        }
        if not _exact_fields(claim, required_claim, {"missingEvidence"}):
            errors.append("{} has invalid fields".format(prefix))
            continue
        claim_id = claim["id"]
        if not isinstance(claim_id, str) or not REPOSITORY_ID.fullmatch(claim_id):
            errors.append("{}.id must be lowercase kebab-case".format(prefix))
        elif claim_id in claim_ids:
            errors.append("{}.id is duplicated".format(prefix))
        if isinstance(claim_id, str):
            claim_ids.add(claim_id)
        if not _is_non_empty_string(claim["statement"]):
            errors.append("{}.statement must be non-empty".format(prefix))
        if not isinstance(claim["status"], str) or claim["status"] not in {
            "confirmed", "inferred", "unknown"
        }:
            errors.append("{}.status is invalid".format(prefix))
        if not isinstance(claim["confidence"], str) or claim["confidence"] not in {
            "high", "medium", "low"
        }:
            errors.append("{}.confidence is invalid".format(prefix))
        if not _is_datetime(claim["analyzedAt"]):
            errors.append("{}.analyzedAt must be a timezone-aware date-time".format(prefix))
        if claim["status"] == "confirmed" and not claim["evidence"]:
            errors.append("{} confirmed claims require evidence".format(prefix))
        if claim["status"] == "unknown" and not claim.get("missingEvidence"):
            errors.append("{} unknown claims require missingEvidence".format(prefix))
        if "missingEvidence" in claim and (
            not isinstance(claim["missingEvidence"], list)
            or any(not _is_non_empty_string(value) for value in claim["missingEvidence"])
        ):
            errors.append("{}.missingEvidence must contain non-empty strings".format(prefix))
        for evidence_field in ("evidence", "counterevidence"):
            values = claim[evidence_field]
            if not isinstance(values, list):
                errors.append("{}.{} must be an array".format(prefix, evidence_field))
                continue
            for evidence_index, item in enumerate(values):
                evidence_prefix = "{}.{}[{}]".format(prefix, evidence_field, evidence_index)
                if not _exact_fields(
                    item, {"repository", "commit", "path"}, {"lines", "observationId"}
                ):
                    errors.append("{} has invalid fields".format(evidence_prefix))
                    continue
                if item["repository"] != repository or item["commit"] != commit:
                    errors.append("{} does not match the envelope".format(evidence_prefix))
                errors.extend(validate_source_reference(
                    {key: item[key] for key in ("path", "lines") if key in item},
                    evidence_prefix,
                    False,
                ))
                observation_id = item.get("observationId")
                if observation_id is not None and (
                    not isinstance(observation_id, str) or not SHA256.fullmatch(observation_id)
                ):
                    errors.append("{}.observationId is invalid".format(evidence_prefix))

    if evidence_bundle is not None:
        if document["evidenceBundleId"] != evidence_bundle.get("id"):
            errors.append("evidenceBundleId does not match the supplied bundle")
        if (
            repository != evidence_bundle.get("repository")
            or commit != evidence_bundle.get("commit")
        ):
            errors.append("candidate repository or commit does not match the supplied bundle")
        selected = {
            (item["path"], item["lines"], observation_id)
            for item in evidence_bundle.get("selectedEvidence", [])
            for observation_id in item.get("observationIds", [])
        }
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                continue
            for field in ("evidence", "counterevidence"):
                for item in claim.get(field, []):
                    if not isinstance(item, dict):
                        continue
                    key = (item.get("path"), item.get("lines"), item.get("observationId"))
                    if key not in selected:
                        message = (
                            "proposedProfile.claims[{}].{} references evidence outside "
                            "the bundle"
                        ).format(index, field)
                        errors.append(message)
    return errors
