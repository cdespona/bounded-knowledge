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
API_KINDS = {"api-document", "api-operation", "api-gap"}
KAFKA_KINDS = {"kafka-topic-reference", "kafka-schema-reference", "kafka-gap"}
API_SPECIFICATIONS = {"openapi", "asyncapi"}
API_SERIALIZATIONS = {"json", "yaml"}
HTTP_ACTIONS = {
    "delete", "get", "head", "options", "patch", "post", "put", "query", "trace"
}
KAFKA_TOPIC_ROLE_FORMS = {
    "declaration": "new-topic",
    "producer-send": "kafka-template-send",
    "producer-default": "default-topic-property",
    "consumer-registration": "kafka-listener",
}
KAFKA_GAP_CONTEXTS = {
    "topic-declaration",
    "producer-send",
    "consumer-registration",
    "schema-reference",
    "configuration",
}
KAFKA_GAP_FORMS = {
    "new-topic",
    "kafka-template-send",
    "kafka-listener",
    "schema-registry-subject-lookup",
    "default-topic-property",
    "kafka-source",
}
CATALOG_COLLECTION_TYPES = {
    "applications": "application",
    "deployables": "deployable",
    "boundedContexts": "bounded-context",
    "useCases": "use-case",
    "terms": "term",
    "strategicCapabilities": "strategic-capability",
}
CATALOG_RELATIONSHIPS = {
    "application-comprises-deployable": ("application", "deployable"),
    "application-participates-in-bounded-context": (
        "application", "bounded-context"
    ),
    "application-supports-use-case": ("application", "use-case"),
    "bounded-context-defines-term": ("bounded-context", "term"),
    "application-contributes-to-strategic-capability": (
        "application", "strategic-capability"
    ),
}
DEPLOYABLE_KINDS = {
    "service", "worker", "job", "function", "scheduled-process", "other"
}
STRATEGY_TYPES = {"objective", "capability", "principle", "constraint", "initiative"}


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


def _line_bounds(value):
    parts = value.split("-", 1)
    return int(parts[0]), int(parts[-1])


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


def validate_api_observation(item, prefix="observation"):
    """Validate the kind-specific contract for a literal API observation."""
    if not isinstance(item, dict):
        return ["{} must be an object".format(prefix)]
    kind = item.get("kind")
    if not isinstance(kind, str) or kind not in API_KINDS:
        return ["{}.kind is not an API observation kind".format(prefix)]

    errors = validate_source_reference(item.get("source"), prefix + ".source", True)
    value = item.get("value")
    if not isinstance(value, dict):
        return errors + ["{}.value must be an object".format(prefix)]

    specification = value.get("specification")
    if kind == "api-gap":
        required = {"specification", "serialization", "code", "detail"}
        if not _exact_fields(value, required):
            errors.append("{}.value has invalid api-gap fields".format(prefix))
        if specification not in API_SPECIFICATIONS | {"unknown"}:
            errors.append("{}.value.specification is invalid".format(prefix))
        if value.get("serialization") not in API_SERIALIZATIONS:
            errors.append("{}.value.serialization is invalid".format(prefix))
        if value.get("serialization") == "yaml" and specification == "unknown":
            errors.append(
                "{}.value.specification cannot be unknown for a YAML filename gap".format(
                    prefix
                )
            )
        code = value.get("code")
        if not isinstance(code, str) or not REPOSITORY_ID.fullmatch(code):
            errors.append("{}.value.code must be lowercase kebab-case".format(prefix))
        if not _is_non_empty_string(value.get("detail")):
            errors.append("{}.value.detail must be a non-empty string".format(prefix))
        return errors

    if specification not in API_SPECIFICATIONS:
        errors.append("{}.value.specification is invalid".format(prefix))

    if kind == "api-document":
        required = {"specification", "serialization", "specificationVersion"}
        if not _exact_fields(value, required, {"title"}):
            errors.append("{}.value has invalid api-document fields".format(prefix))
        if value.get("serialization") != "json":
            errors.append("{}.value.serialization must be json".format(prefix))
        if not _is_non_empty_string(value.get("specificationVersion")):
            errors.append(
                "{}.value.specificationVersion must be a non-empty string".format(prefix)
            )
        if "title" in value and not _is_non_empty_string(value["title"]):
            errors.append("{}.value.title must be a non-empty string".format(prefix))
        return errors

    operation_type = value.get("operationType")
    action = value.get("action")
    target = value.get("target")
    if specification == "openapi":
        required = {"specification", "operationType", "action", "target"}
        if not _exact_fields(value, required, {"operationId"}):
            errors.append("{}.value has invalid OpenAPI operation fields".format(prefix))
        if operation_type != "http":
            errors.append("{}.value.operationType must be http".format(prefix))
        if action not in HTTP_ACTIONS:
            errors.append("{}.value.action is not a supported HTTP action".format(prefix))
        if not _is_non_empty_string(target) or not target.startswith("/"):
            errors.append("{}.value.target must be an HTTP path".format(prefix))
    elif action in {"publish", "subscribe"}:
        required = {"specification", "operationType", "action", "target"}
        if not _exact_fields(value, required, {"operationId"}):
            errors.append("{}.value has invalid AsyncAPI 2 operation fields".format(prefix))
        if operation_type != "channel":
            errors.append("{}.value.operationType must be channel".format(prefix))
        if not _is_non_empty_string(target):
            errors.append("{}.value.target must be a channel key".format(prefix))
    else:
        required = {
            "specification", "operationType", "operationKey", "action", "target"
        }
        if not _exact_fields(value, required):
            errors.append("{}.value has invalid AsyncAPI 3 operation fields".format(prefix))
        if operation_type != "channel":
            errors.append("{}.value.operationType must be channel".format(prefix))
        if action not in {"send", "receive"}:
            errors.append("{}.value.action must be send or receive".format(prefix))
        if not _is_non_empty_string(value.get("operationKey")):
            errors.append("{}.value.operationKey must be a non-empty string".format(prefix))
        if not _is_non_empty_string(target):
            errors.append("{}.value.target must be a literal channel reference".format(prefix))

    if "operationId" in value and not _is_non_empty_string(value["operationId"]):
        errors.append("{}.value.operationId must be a non-empty string".format(prefix))
    return errors


def validate_kafka_observation(item, prefix="observation"):
    """Validate the kind-specific contract for a literal Kafka observation."""
    if not isinstance(item, dict):
        return ["{} must be an object".format(prefix)]
    kind = item.get("kind")
    if not isinstance(kind, str) or kind not in KAFKA_KINDS:
        return ["{}.kind is not a Kafka observation kind".format(prefix)]

    errors = validate_source_reference(item.get("source"), prefix + ".source", True)
    value = item.get("value")
    if not isinstance(value, dict):
        return errors + ["{}.value must be an object".format(prefix)]

    if kind == "kafka-topic-reference":
        if not _exact_fields(value, {"role", "form", "topic"}):
            errors.append("{}.value has invalid kafka-topic-reference fields".format(prefix))
        role = value.get("role")
        if role not in KAFKA_TOPIC_ROLE_FORMS:
            errors.append("{}.value.role is invalid".format(prefix))
        elif value.get("form") != KAFKA_TOPIC_ROLE_FORMS[role]:
            errors.append("{}.value.role and form disagree".format(prefix))
        if not _is_non_empty_string(value.get("topic")):
            errors.append("{}.value.topic must be a non-empty string".format(prefix))
    elif kind == "kafka-schema-reference":
        if not _exact_fields(value, {"role", "form", "subject"}):
            errors.append("{}.value has invalid kafka-schema-reference fields".format(prefix))
        if value.get("role") != "schema-lookup":
            errors.append("{}.value.role must be schema-lookup".format(prefix))
        if value.get("form") != "schema-registry-subject-lookup":
            errors.append(
                "{}.value.form must be schema-registry-subject-lookup".format(prefix)
            )
        if not _is_non_empty_string(value.get("subject")):
            errors.append("{}.value.subject must be a non-empty string".format(prefix))
    else:
        required = {"context", "form", "code", "detail"}
        if not _exact_fields(value, required):
            errors.append("{}.value has invalid kafka-gap fields".format(prefix))
        if value.get("context") not in KAFKA_GAP_CONTEXTS:
            errors.append("{}.value.context is invalid".format(prefix))
        if value.get("form") not in KAFKA_GAP_FORMS:
            errors.append("{}.value.form is invalid".format(prefix))
        code = value.get("code")
        if not isinstance(code, str) or not REPOSITORY_ID.fullmatch(code):
            errors.append("{}.value.code must be lowercase kebab-case".format(prefix))
        if not _is_non_empty_string(value.get("detail")):
            errors.append("{}.value.detail must be a non-empty string".format(prefix))
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
        status = claim["status"]
        if not isinstance(status, str) or status not in {
            "confirmed", "inferred", "unknown"
        }:
            errors.append("{}.status is invalid".format(prefix))
        if not isinstance(claim["confidence"], str) or claim["confidence"] not in {
            "high", "medium", "low"
        }:
            errors.append("{}.confidence is invalid".format(prefix))
        if not _is_datetime(claim["analyzedAt"]):
            errors.append("{}.analyzedAt must be a timezone-aware date-time".format(prefix))
        if status in ("confirmed", "inferred") and not claim["evidence"]:
            errors.append("{} {} claims require evidence".format(prefix, status))
        if status == "unknown" and not claim.get("missingEvidence"):
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
                if "lines" not in item:
                    errors.append("{}.lines is required for bundle verification".format(
                        evidence_prefix
                    ))
                if "observationId" not in item:
                    errors.append(
                        "{}.observationId is required for bundle verification".format(
                            evidence_prefix
                        )
                    )

    if evidence_bundle is not None:
        if document["evidenceBundleId"] != evidence_bundle.get("id"):
            errors.append("evidenceBundleId does not match the supplied bundle")
        if repository != evidence_bundle.get("repository"):
            errors.append("candidate repository does not match the supplied bundle")
        if kind != evidence_bundle.get("kind"):
            errors.append("candidate kind does not match the supplied bundle")
        if commit != evidence_bundle.get("commit"):
            errors.append("candidate commit does not match the supplied bundle")
        selected = {}
        for item in evidence_bundle.get("selectedEvidence", []):
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            lines = item.get("lines")
            observation_ids = item.get("observationIds", [])
            if (
                isinstance(path, str)
                and _valid_lines(lines)
                and isinstance(observation_ids, list)
            ):
                selected.setdefault(path, []).append((lines, set(observation_ids)))
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                continue
            for field in ("evidence", "counterevidence"):
                for item in claim.get(field, []):
                    if not isinstance(item, dict):
                        continue
                    path = item.get("path")
                    lines = item.get("lines")
                    observation_id = item.get("observationId")
                    matched = False
                    if (
                        isinstance(path, str)
                        and _valid_lines(lines)
                        and isinstance(observation_id, str)
                    ):
                        reference_start, reference_end = _line_bounds(lines)
                        for selected_lines, observation_ids in selected.get(path, []):
                            selected_start, selected_end = _line_bounds(selected_lines)
                            if (
                                selected_start <= reference_start <= reference_end <= selected_end
                                and observation_id in observation_ids
                            ):
                                matched = True
                                break
                    if not matched:
                        message = (
                            "proposedProfile.claims[{}].{} references evidence outside "
                            "the bundle"
                        ).format(index, field)
                        errors.append(message)
    return errors


def _sorted_unique_strings(value):
    return (
        isinstance(value, list)
        and all(_is_non_empty_string(item) for item in value)
        and value == sorted(set(value))
    )


def _knowledge_evidence_key(value):
    if not isinstance(value, dict):
        return ("", json.dumps(value, sort_keys=True))
    if value.get("kind") == "repository":
        return (
            "repository",
            str(value.get("repositoryId", "")),
            str(value.get("commit", "")),
            str(value.get("path", "")),
            str(value.get("lines", "")),
            str(value.get("observationId", "")),
            str(value.get("sourceSelectionId", "")),
        )
    return (
        str(value.get("kind", "")),
        str(value.get("sourceId", "")),
        str(value.get("sourceType", "")),
        str(value.get("recordedAt", "")),
        str(value.get("locator", "")),
        str(value.get("suppliedBy", "")),
    )


def _validate_knowledge_evidence(value, prefix):
    if not isinstance(value, dict):
        return ["{} must be an object".format(prefix)]
    errors = []
    kind = value.get("kind")
    if kind == "repository":
        required = {"kind", "repositoryId", "commit", "path"}
        optional = {"sourceSelectionId", "lines", "observationId"}
        if not _exact_fields(value, required, optional):
            return ["{} has invalid repository evidence fields".format(prefix)]
        for field in ("repositoryId", "sourceSelectionId"):
            if field in value and (
                not isinstance(value[field], str)
                or not REPOSITORY_ID.fullmatch(value[field])
            ):
                errors.append("{}.{} must be lowercase kebab-case".format(prefix, field))
        if not isinstance(value["commit"], str) or not SHA.fullmatch(value["commit"]):
            errors.append("{}.commit must be a full hexadecimal Git SHA".format(prefix))
        errors.extend(validate_source_reference(
            {key: value[key] for key in ("path", "lines") if key in value},
            prefix,
        ))
        observation_id = value.get("observationId")
        if observation_id is not None and (
            not isinstance(observation_id, str) or not SHA256.fullmatch(observation_id)
        ):
            errors.append("{}.observationId is invalid".format(prefix))
    elif kind == "curated":
        required = {"kind", "sourceId", "sourceType", "suppliedBy", "recordedAt"}
        if not _exact_fields(value, required, {"locator"}):
            return ["{} has invalid curated evidence fields".format(prefix)]
        if not isinstance(value["sourceId"], str) or not REPOSITORY_ID.fullmatch(
            value["sourceId"]
        ):
            errors.append("{}.sourceId must be lowercase kebab-case".format(prefix))
        if not isinstance(value["sourceType"], str) or value["sourceType"] not in {
            "interview", "document", "approved-reference"
        }:
            errors.append("{}.sourceType is invalid".format(prefix))
        if not _is_non_empty_string(value["suppliedBy"]):
            errors.append("{}.suppliedBy must be non-empty".format(prefix))
        if not _is_datetime(value["recordedAt"]):
            errors.append("{}.recordedAt must be a timezone-aware date-time".format(prefix))
        if "locator" in value and not _is_non_empty_string(value["locator"]):
            errors.append("{}.locator must be non-empty".format(prefix))
    else:
        errors.append("{}.kind must be repository or curated".format(prefix))
    return errors


def _validate_assessment(value, prefix):
    required = {"status", "confidence", "evidence", "counterevidence", "analyzedAt"}
    if not _exact_fields(value, required, {"missingEvidence"}):
        return ["{} has invalid fields".format(prefix)]
    errors = []
    status = value["status"]
    if not isinstance(status, str) or status not in {"confirmed", "inferred", "unknown"}:
        errors.append("{}.status is invalid".format(prefix))
    if not isinstance(value["confidence"], str) or value["confidence"] not in {
        "high", "medium", "low"
    }:
        errors.append("{}.confidence is invalid".format(prefix))
    if not _is_datetime(value["analyzedAt"]):
        errors.append("{}.analyzedAt must be a timezone-aware date-time".format(prefix))
    for field in ("evidence", "counterevidence"):
        items = value[field]
        if not isinstance(items, list):
            errors.append("{}.{} must be an array".format(prefix, field))
            continue
        for index, item in enumerate(items):
            errors.extend(_validate_knowledge_evidence(
                item, "{}.{}[{}]".format(prefix, field, index)
            ))
        keys = [_knowledge_evidence_key(item) for item in items]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            errors.append("{}.{} must be sorted and unique".format(prefix, field))
    if isinstance(status, str) and status in {"confirmed", "inferred"} and not value["evidence"]:
        errors.append("{} {} assessments require evidence".format(prefix, status))
    if status == "unknown" and not value.get("missingEvidence"):
        errors.append("{} unknown assessments require missingEvidence".format(prefix))
    if "missingEvidence" in value and (
        not value["missingEvidence"]
        or not _sorted_unique_strings(value["missingEvidence"])
    ):
        errors.append("{}.missingEvidence must be sorted and unique".format(prefix))
    return errors


def _has_curated_support(value):
    if not isinstance(value, dict):
        return False
    if value.get("status") == "unknown":
        return True
    evidence = value.get("evidence")
    return isinstance(evidence, list) and any(
        isinstance(item, dict) and item.get("kind") == "curated"
        for item in evidence
    )


def validate_landscape_catalog(document):
    """Validate the canonical logical landscape without resolving source topology."""
    collections = set(CATALOG_COLLECTION_TYPES)
    required = {"schemaVersion", "relationships", "openQuestions"} | collections
    if not _exact_fields(document, required):
        return ["landscape catalog has invalid fields"]
    errors = []
    if type(document["schemaVersion"]) is not int or document["schemaVersion"] != 1:
        errors.append("schemaVersion must be 1")
    entities = {}
    all_ids = set()
    required_fields = {
        "applications": {"id", "name", "purpose", "assessment"},
        "deployables": {"id", "name", "kind", "assessment"},
        "boundedContexts": {"id", "name", "description", "assessment"},
        "useCases": {"id", "name", "actor", "outcome", "assessment"},
        "terms": {"id", "name", "definition", "assessment"},
        "strategicCapabilities": {
            "id", "name", "type", "statement", "desiredOutcomes", "assessment"
        },
    }
    for collection, entity_type in CATALOG_COLLECTION_TYPES.items():
        values = document[collection]
        if not isinstance(values, list):
            errors.append("{} must be an array".format(collection))
            continue
        order = []
        for index, item in enumerate(values):
            prefix = "{}[{}]".format(collection, index)
            optional = {"timeHorizon", "priority"} if collection == "strategicCapabilities" else set()
            if not _exact_fields(item, required_fields[collection], optional):
                errors.append("{} has invalid fields".format(prefix))
                continue
            entity_id = item["id"]
            if not isinstance(entity_id, str) or not REPOSITORY_ID.fullmatch(entity_id):
                errors.append("{}.id must be lowercase kebab-case".format(prefix))
            else:
                order.append(entity_id)
                if entity_id in all_ids:
                    errors.append("{}.id is duplicated across the catalog".format(prefix))
                all_ids.add(entity_id)
                entities[(entity_type, entity_id)] = item
            for field in required_fields[collection] - {"id", "assessment", "desiredOutcomes"}:
                if field not in {"kind", "type"} and not _is_non_empty_string(item[field]):
                    errors.append("{}.{} must be non-empty".format(prefix, field))
            if collection == "deployables" and (
                not isinstance(item["kind"], str) or item["kind"] not in DEPLOYABLE_KINDS
            ):
                errors.append("{}.kind is invalid".format(prefix))
            if collection == "strategicCapabilities":
                if not isinstance(item["type"], str) or item["type"] not in STRATEGY_TYPES:
                    errors.append("{}.type is invalid".format(prefix))
                if (
                    not item["desiredOutcomes"]
                    or not _sorted_unique_strings(item["desiredOutcomes"])
                ):
                    errors.append("{}.desiredOutcomes must be sorted and unique".format(prefix))
                for field in ("timeHorizon",):
                    if field in item and not _is_non_empty_string(item[field]):
                        errors.append("{}.{} must be non-empty".format(prefix, field))
                if "priority" in item and (
                    not isinstance(item["priority"], str)
                    or item["priority"] not in {"high", "medium", "low"}
                ):
                    errors.append("{}.priority is invalid".format(prefix))
            errors.extend(_validate_assessment(item["assessment"], prefix + ".assessment"))
            if collection == "strategicCapabilities" and not _has_curated_support(
                item["assessment"]
            ):
                errors.append("{}.assessment requires curated supporting evidence".format(prefix))
        if order != sorted(order):
            errors.append("{} must be sorted by id".format(collection))

    relationships = document["relationships"]
    if not isinstance(relationships, list):
        errors.append("relationships must be an array")
    else:
        order = []
        identities = set()
        for index, item in enumerate(relationships):
            prefix = "relationships[{}]".format(index)
            required_relationship = {
                "id", "type", "source", "target", "statement", "assessment"
            }
            if not _exact_fields(item, required_relationship):
                errors.append("{} has invalid fields".format(prefix))
                continue
            relationship_id = item["id"]
            if not isinstance(relationship_id, str) or not REPOSITORY_ID.fullmatch(
                relationship_id
            ):
                errors.append("{}.id must be lowercase kebab-case".format(prefix))
            else:
                if relationship_id in all_ids:
                    errors.append("{}.id is duplicated across the catalog".format(prefix))
                all_ids.add(relationship_id)
                order.append((str(item["type"]), relationship_id))
            relationship_type = item["type"]
            expected = (
                CATALOG_RELATIONSHIPS.get(relationship_type)
                if isinstance(relationship_type, str)
                else None
            )
            if expected is None:
                errors.append("{}.type is invalid".format(prefix))
                expected = (None, None)
            endpoints = []
            for field, expected_type in zip(("source", "target"), expected):
                endpoint = item[field]
                endpoint_prefix = "{}.{}".format(prefix, field)
                if not _exact_fields(endpoint, {"entityType", "entityId"}):
                    errors.append("{} has invalid fields".format(endpoint_prefix))
                    endpoints.append((None, None))
                    continue
                endpoint_type = endpoint["entityType"]
                endpoint_id = endpoint["entityId"]
                endpoints.append((endpoint_type, endpoint_id))
                if endpoint_type != expected_type:
                    errors.append("{} has an invalid entity type".format(endpoint_prefix))
                if (
                    not isinstance(endpoint_type, str)
                    or not isinstance(endpoint_id, str)
                    or (endpoint_type, endpoint_id) not in entities
                ):
                    errors.append("{} references an unknown entity".format(endpoint_prefix))
            identity = (str(relationship_type),) + tuple(
                (str(endpoint_type), str(endpoint_id))
                for endpoint_type, endpoint_id in endpoints
            )
            if identity in identities:
                errors.append("{} duplicates a relationship".format(prefix))
            identities.add(identity)
            if not _is_non_empty_string(item["statement"]):
                errors.append("{}.statement must be non-empty".format(prefix))
            errors.extend(_validate_assessment(item["assessment"], prefix + ".assessment"))
            if (
                item["type"] == "application-contributes-to-strategic-capability"
                and not _has_curated_support(item["assessment"])
            ):
                errors.append("{}.assessment requires curated supporting evidence".format(prefix))
        if order != sorted(order):
            errors.append("relationships must be sorted by type and id")
    if not _sorted_unique_strings(document["openQuestions"]):
        errors.append("openQuestions must be sorted and unique")
    return errors


def _is_safe_topology_path(value):
    if value == ".":
        return True
    if (
        not _is_non_empty_string(value)
        or value.startswith("/")
        or value.endswith("/")
        or "//" in value
        or "\\" in value
    ):
        return False
    return all(
        part not in {"", ".", ".."}
        and re.fullmatch(r"[A-Za-z0-9._-]+", part) is not None
        for part in value.split("/")
    )


def _path_is_at_or_below(child, parent):
    if child == ".":
        return False
    child_parts = PurePosixPath(child).parts
    parent_parts = PurePosixPath(parent).parts
    return child_parts[:len(parent_parts)] == parent_parts


def validate_source_topology(document, source_registry=None, landscape_catalog=None):
    """Validate portable source selections and their catalog bindings."""
    if source_registry is not None:
        dependency_errors = validate_source_registry(source_registry)
        if dependency_errors:
            return ["source registry: " + error for error in dependency_errors]
    if landscape_catalog is not None:
        dependency_errors = validate_landscape_catalog(landscape_catalog)
        if dependency_errors:
            return ["landscape catalog: " + error for error in dependency_errors]
    if not _exact_fields(
        document, {"schemaVersion", "repositories", "sourceSelections", "bindings"}
    ):
        return ["source topology has invalid fields"]
    errors = []
    if type(document["schemaVersion"]) is not int or document["schemaVersion"] != 1:
        errors.append("schemaVersion must be 1")

    registry = {}
    if source_registry is not None:
        registry = {item["id"]: item for item in source_registry["repositories"]}
    repositories = {}
    repository_values = document["repositories"]
    if not isinstance(repository_values, list):
        errors.append("repositories must be an array")
        repository_values = []
    repository_order = []
    for index, item in enumerate(repository_values):
        prefix = "repositories[{}]".format(index)
        if not _exact_fields(item, {"id", "kind"}):
            errors.append("{} has invalid fields".format(prefix))
            continue
        repository_id = item["id"]
        if not isinstance(repository_id, str) or not REPOSITORY_ID.fullmatch(repository_id):
            errors.append("{}.id must be lowercase kebab-case".format(prefix))
        else:
            repository_order.append(repository_id)
            if repository_id in repositories:
                errors.append("{}.id is duplicated".format(prefix))
            repositories[repository_id] = item
        if not isinstance(item["kind"], str) or item["kind"] not in KINDS:
            errors.append("{}.kind is invalid".format(prefix))
        registered = registry.get(repository_id) if isinstance(repository_id, str) else None
        if source_registry is not None:
            if registered is None:
                errors.append("{}.id is not present in the source registry".format(prefix))
            elif not registered["enabled"]:
                errors.append("{}.id references a disabled source".format(prefix))
            elif registered["kind"] != item["kind"]:
                errors.append("{}.kind does not match the source registry".format(prefix))
    if repository_order != sorted(repository_order):
        errors.append("repositories must be sorted by id")

    selections = {}
    selection_identities = set()
    selection_values = document["sourceSelections"]
    if not isinstance(selection_values, list):
        errors.append("sourceSelections must be an array")
        selection_values = []
    selection_order = []
    for index, item in enumerate(selection_values):
        prefix = "sourceSelections[{}]".format(index)
        if not _exact_fields(item, {"id", "repositoryId", "subpath", "kind"}):
            errors.append("{} has invalid fields".format(prefix))
            continue
        selection_id = item["id"]
        if not isinstance(selection_id, str) or not REPOSITORY_ID.fullmatch(selection_id):
            errors.append("{}.id must be lowercase kebab-case".format(prefix))
        else:
            selection_order.append(selection_id)
            if selection_id in selections or selection_id in repositories:
                errors.append("{}.id is duplicated".format(prefix))
            selections[selection_id] = item
        repository_id = item["repositoryId"]
        repository = repositories.get(repository_id) if isinstance(repository_id, str) else None
        if repository is None:
            errors.append("{}.repositoryId references an unknown repository".format(prefix))
        elif item["kind"] != repository["kind"]:
            errors.append("{}.kind does not match its repository".format(prefix))
        if not isinstance(item["kind"], str) or item["kind"] not in KINDS:
            errors.append("{}.kind is invalid".format(prefix))
        if not _is_safe_topology_path(item["subpath"]):
            errors.append("{}.subpath must be a safe relative path".format(prefix))
        identity = (str(repository_id), str(item["subpath"]), str(item["kind"]))
        if identity in selection_identities:
            errors.append("{} duplicates a source selection".format(prefix))
        selection_identities.add(identity)
        registered = registry.get(repository_id) if isinstance(repository_id, str) else None
        if registered is not None and _is_safe_topology_path(item["subpath"]):
            for exclusion in registered.get("exclude", []):
                if _path_is_at_or_below(item["subpath"], exclusion):
                    errors.append("{}.subpath is excluded by the source registry".format(prefix))
                    break
    if selection_order != sorted(selection_order):
        errors.append("sourceSelections must be sorted by id")

    catalog_targets = set()
    if landscape_catalog is not None:
        for collection, entity_type in CATALOG_COLLECTION_TYPES.items():
            catalog_targets.update(
                (entity_type, item["id"]) for item in landscape_catalog[collection]
                if entity_type in {"application", "deployable"}
            )
    binding_values = document["bindings"]
    if not isinstance(binding_values, list):
        errors.append("bindings must be an array")
        binding_values = []
    binding_order = []
    binding_identities = set()
    for index, item in enumerate(binding_values):
        prefix = "bindings[{}]".format(index)
        if not _exact_fields(item, {"sourceSelectionId", "target", "status", "provenance"}):
            errors.append("{} has invalid fields".format(prefix))
            continue
        selection_id = item["sourceSelectionId"]
        if not isinstance(selection_id, str) or selection_id not in selections:
            errors.append("{}.sourceSelectionId references an unknown selection".format(prefix))
        target = item["target"]
        if not _exact_fields(target, {"type", "id"}):
            errors.append("{}.target has invalid fields".format(prefix))
            continue
        target_type = target["type"]
        target_id = target["id"]
        if not isinstance(target_type, str) or target_type not in {
            "application", "deployable"
        }:
            errors.append("{}.target.type is invalid".format(prefix))
        if not isinstance(target_id, str) or not REPOSITORY_ID.fullmatch(target_id):
            errors.append("{}.target.id must be lowercase kebab-case".format(prefix))
        if landscape_catalog is not None and (
            not isinstance(target_type, str)
            or not isinstance(target_id, str)
            or (target_type, target_id) not in catalog_targets
        ):
            errors.append("{}.target references an unknown catalog entity".format(prefix))
        identity = (str(selection_id), str(target_type), str(target_id))
        binding_order.append(identity)
        if identity in binding_identities:
            errors.append("{} duplicates a binding".format(prefix))
        binding_identities.add(identity)
        provenance = item["provenance"]
        if not _exact_fields(provenance, {"method", "detail"}):
            errors.append("{}.provenance has invalid fields".format(prefix))
            continue
        expected_method = (
            {"declared": "manual", "inferred": "folder-convention"}.get(item["status"])
            if isinstance(item["status"], str)
            else None
        )
        if expected_method is None:
            errors.append("{}.status is invalid".format(prefix))
        elif provenance["method"] != expected_method:
            errors.append("{}.status and provenance method disagree".format(prefix))
        if not _is_non_empty_string(provenance["detail"]):
            errors.append("{}.provenance.detail must be non-empty".format(prefix))
    if binding_order != sorted(binding_order):
        errors.append("bindings must be sorted by selection and target")

    if landscape_catalog is not None:
        assessed = []
        for collection in CATALOG_COLLECTION_TYPES:
            assessed.extend(
                ("{}[{}]".format(collection, index), item.get("assessment"))
                for index, item in enumerate(landscape_catalog[collection])
            )
        assessed.extend(
            ("relationships[{}]".format(index), item.get("assessment"))
            for index, item in enumerate(landscape_catalog["relationships"])
        )
        for catalog_prefix, assessment in assessed:
            for field in ("evidence", "counterevidence"):
                for index, evidence in enumerate(assessment.get(field, [])):
                    if evidence.get("kind") != "repository":
                        continue
                    prefix = "landscape catalog {}.{}[{}]".format(
                        catalog_prefix, field, index
                    )
                    repository_id = evidence["repositoryId"]
                    if repository_id not in repositories:
                        errors.append("{} references an unknown topology repository".format(prefix))
                    selection_id = evidence.get("sourceSelectionId")
                    if selection_id is not None:
                        selection = selections.get(selection_id)
                        if selection is None:
                            errors.append("{} references an unknown source selection".format(prefix))
                        elif selection["repositoryId"] != repository_id:
                            errors.append("{} source selection belongs to another repository".format(prefix))
    return errors
