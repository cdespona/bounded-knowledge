"""Deterministic, bounded evidence selection from a validated inventory."""

import hashlib
import json
from pathlib import Path, PurePosixPath

from .config_files import is_config_path, mask_account_ids, mask_config_text
from .contracts import artifact_id, validate_evidence_bundle
from .git_repository import commit_sha, dirty_paths, require_repository_root
from .safety import MAX_FILE_BYTES, copilot_configuration_files, exclusion_reason
from .validation import validate_inventory


MAX_SELECTED_FILES = 256
MAX_RANGES_PER_FILE = 16
MAX_LINES_PER_SELECTION = 200
MAX_CONTENT_BYTES = 16 * 1024
MAX_TOTAL_CONTENT_BYTES = 2 * 1024 * 1024
GAP_KINDS = {"api-gap", "container-gap", "config-gap", "io-gap", "kafka-gap", "manifest-gap", "terraform-gap"}


def _candidate_category(observation_ids, observations_by_id):
    """Return a stable, literal-evidence category for one candidate range."""
    kinds = {observations_by_id[item_id]["kind"] for item_id in observation_ids}
    if kinds & {"build-project", "declared-dependency", "build-plugin", "build-module", "manifest-file"}:
        return "manifest"
    if any(kind.startswith("api-") for kind in kinds):
        return "api-contract"
    if any(kind.startswith("kafka-") for kind in kinds):
        return "kafka-literal"
    if any(kind.startswith("io-") for kind in kinds):
        return "io-point"
    if any(kind.startswith("config-") for kind in kinds):
        return "config-literal"
    if any(kind.startswith("container-") for kind in kinds):
        return "container-literal"
    return "repository-file"


_CATEGORY_PRIORITY = {"manifest": 0, "api-contract": 1, "kafka-literal": 2, "io-point": 3, "config-literal": 4,
                      "container-literal": 5, "repository-file": 6}

_SEMANTIC_PRIORITY = {"api-contract": 0, "kafka-literal": 1, "io-point": 2, "config-literal": 3, "entrypoint": 4,
                      "port": 5, "schema-declaration": 6, "application-service": 7,
                      "message": 8, "persistence": 9, "domain-type": 10,
                      "mapper": 11, "outside-semantic-scope": 12}


def _semantic_category(candidate):
    """Rank safe inventory paths by code structure, not inferred business meaning."""
    path = PurePosixPath(candidate["path"])
    parts = tuple(part.lower() for part in path.parts)
    name = path.stem.lower()
    if any(part in {"test", "tests", "fixture", "fixtures"} for part in parts):
        return "outside-semantic-scope"
    if candidate["category"] in {"api-contract", "kafka-literal", "io-point", "config-literal"}:
        return candidate["category"]
    main_source = any(parts[index:index + 2] == ("src", "main") for index in range(len(parts) - 1))
    if not main_source:
        return "outside-semantic-scope"
    if path.suffix.lower() in {".avsc", ".proto"}:
        return "schema-declaration"
    if path.suffix.lower() not in {".java", ".kt"} or not any(
        parts[index:index + 3] in {("src", "main", "java"), ("src", "main", "kotlin")}
        for index in range(len(parts) - 2)
    ):
        return "outside-semantic-scope"
    if any(part in {"consumer", "controller", "listener", "stream"} for part in parts) or any(
        word in name for word in ("consumer", "controller", "listener", "stream", "application")
    ):
        return "entrypoint"
    if "mapper" in parts or "mapper" in name:
        return "mapper"
    if any(part in {"application", "service", "usecase", "usecases"} for part in parts) or any(
        word in name for word in ("service", "usecase")
    ):
        return "application-service"
    if any(part in {"port", "ports"} for part in parts):
        return "port"
    if any(part in {"message", "messages", "event", "events", "schema", "schemas"} for part in parts):
        return "message"
    if any(part in {"persistence", "repository", "repositories", "database"} for part in parts):
        return "persistence"
    if any(part in {"domain", "entity", "entities", "model", "value"} for part in parts):
        return "domain-type"
    return "outside-semantic-scope"


def _selection_coverage(candidates, selected, omissions, selected_candidate_keys):
    """Summarize candidate coverage without asserting semantic completeness."""
    selected_keys = set(selected_candidate_keys)
    selected_paths = {item["path"] for item in selected}
    by_category = []
    for category in sorted({item["category"] for item in candidates}):
        category_candidates = [item for item in candidates if item["category"] == category]
        paths = {item["path"] for item in category_candidates}
        selected_ranges = sum((item["path"], item["lines"]) in selected_keys for item in category_candidates)
        by_category.append({"category": category, "candidatePaths": len(paths),
                            "candidateRanges": len(category_candidates),
                            "selectedPaths": len(paths & selected_paths),
                            "selectedRanges": selected_ranges,
                            "omittedPaths": len(paths - selected_paths)})
    return {
        "candidatePaths": len({item["path"] for item in candidates}),
        "candidateRanges": len(candidates), "selectedPaths": len(selected_paths),
        "selectedRanges": len(selected),
        "omittedPaths": len({item["path"] for item in candidates} - selected_paths),
        "byCategory": by_category,
        "omittedByReason": [
            {"reason": reason, "paths": len({path for path, item_reason in omissions if item_reason == reason}),
             "ranges": sum(item_reason == reason for _, item_reason in omissions)}
            for reason in sorted({reason for _, reason in omissions})
        ],
    }


def inventory_digest(document):
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _line_bounds(value):
    parts = value.split("-", 1)
    return int(parts[0]), int(parts[-1])


def _source_is_safe(root, relative):
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            return False
    try:
        return current.is_file() and current.resolve().is_relative_to(root)
    except AttributeError:  # Python 3.9
        try:
            current.resolve().relative_to(root)
            return current.is_file()
        except ValueError:
            return False


def _inventory_exclusion_reason(relative, excluded):
    path = PurePosixPath(relative)
    for item in sorted(excluded, key=lambda value: (value["path"], value["reason"])):
        excluded_path = PurePosixPath(item["path"])
        if path == excluded_path or excluded_path in path.parents:
            return item["reason"]
    return None


def _bounded_content(lines, start, end):
    requested = lines[start - 1 : end]
    truncated = end - start + 1 > MAX_LINES_PER_SELECTION
    requested = requested[:MAX_LINES_PER_SELECTION]
    content = "\n".join(requested)
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_CONTENT_BYTES:
        encoded = encoded[:MAX_CONTENT_BYTES]
        while True:
            try:
                content = encoded.decode("utf-8")
                break
            except UnicodeDecodeError:
                encoded = encoded[:-1]
        truncated = True
    actual_end = start + max(len(requested) - 1, 0)
    if truncated and "\n" in content:
        actual_end = start + content.count("\n")
    return content, actual_end, truncated


def _bundle_kind(observations, source_selection=None):
    if source_selection is not None:
        return source_selection["kind"]
    return "application" if any(
        item.get("kind") in {"build-project", "declared-dependency", "build-plugin", "build-module"}
        for item in observations
        if isinstance(item, dict)
    ) else "other"


def select_evidence(inventory, source, purpose="general"):
    """Return a validated evidence bundle without modifying the source repository."""
    if purpose not in {"general", "semantic"}:
        raise ValueError("Unsupported evidence selection purpose")
    source_path = Path(source).expanduser()
    if source_path.is_symlink():
        raise ValueError("Source repository root must not be a symbolic link")
    root = require_repository_root(source_path)
    errors = validate_inventory(inventory, source=root)
    if errors:
        raise ValueError("Invalid inventory: {}".format("; ".join(errors)))
    dirty = dirty_paths(root)
    if dirty:
        raise ValueError("Repository must have a clean working tree before evidence selection")

    observations = inventory["observations"]
    observations_by_id = {item["id"]: item for item in observations if isinstance(item, dict)}
    precise_paths = {
        item["source"]["path"]
        for item in observations
        if isinstance(item, dict)
        and isinstance(item.get("source"), dict)
        and item["source"].get("lines")
        and item.get("kind") not in GAP_KINDS
        and not item.get("kind", "").startswith("io-")
    }
    candidates = {}
    whole_file_keys = set()
    gaps = []
    for item in observations:
        source_ref = item.get("source") if isinstance(item, dict) else None
        if not isinstance(source_ref, dict):
            continue
        relative = source_ref["path"]
        if item.get("kind") in GAP_KINDS:
            gaps.append({
                "code": item["value"]["code"],
                "detail": item["value"]["detail"],
                "observationId": item["id"],
                "source": dict(source_ref),
            })
            continue
        if item.get("kind", "").startswith("terraform-") and item.get("value", {}).get("evidenceDisposition") != "raw-text-safe":
            gaps.append({"code":"sensitive-value-withheld","detail":"Source text is withheld by the evidence privacy policy.","observationId":item["id"],"source":dict(source_ref)})
            continue
        source_lines = None if item["kind"].startswith("io-") else source_ref.get("lines")
        if source_lines is None and relative in precise_paths and not item["kind"].startswith(("config-", "io-")):
            continue
        key = (relative, source_lines or "1-200")
        candidates.setdefault(key, []).append(item["id"])
        if source_lines is None:
            whole_file_keys.add(key)

    candidates = [
        {"path": relative, "lines": requested_lines,
         "observationIds": sorted(set(observation_ids)),
         "category": _candidate_category(observation_ids, observations_by_id)}
        for (relative, requested_lines), observation_ids in candidates.items()
    ]
    if purpose == "semantic":
        for candidate in candidates:
            candidate["category"] = _semantic_category(candidate)

    # This is the final HEAD check before any selected source content is read.
    if commit_sha(root) != inventory["commit"]:
        raise ValueError("Inventory commit does not match the source repository HEAD")

    selected = []
    excluded = list(inventory["excluded"])
    excluded_keys = {(item["path"], item["reason"]) for item in excluded}
    omissions = []
    selected_candidate_keys = set()
    customization_paths = set(copilot_configuration_files(root))
    selected_paths = set()
    ranges_by_path = {}
    total_bytes = 0
    priority = _SEMANTIC_PRIORITY if purpose == "semantic" else _CATEGORY_PRIORITY
    for candidate in sorted(candidates, key=lambda item: (priority[item["category"]], item["path"], item["lines"])):
        relative = candidate["path"]
        requested_lines = candidate["lines"]
        observation_ids = candidate["observationIds"]
        relative_path = Path(relative)
        path = root / relative_path
        reason = "source-owned-customization" if relative in customization_paths else None
        if reason is None:
            reason = _inventory_exclusion_reason(relative, inventory["excluded"])
        if reason is None:
            reason = exclusion_reason(relative_path)
        if reason is None and purpose == "semantic" and candidate["category"] == "outside-semantic-scope":
            reason = "semantic-scope"
        if reason is None and not _source_is_safe(root, relative):
            reason = "unsafe-or-symbolic-link"
        if reason is None:
            try:
                size = path.stat().st_size
            except OSError:
                reason = "unreadable-file"
            else:
                reason = exclusion_reason(relative_path, size)
        if reason:
            excluded_keys.add((relative, reason))
            omissions.append((relative, reason))
            continue
        if relative not in selected_paths and len(selected_paths) >= MAX_SELECTED_FILES:
            excluded_keys.add((relative, "selection-file-limit"))
            omissions.append((relative, "selection-file-limit"))
            continue
        if ranges_by_path.get(relative, 0) >= MAX_RANGES_PER_FILE:
            excluded_keys.add((relative, "selection-range-limit"))
            omissions.append((relative, "selection-range-limit"))
            continue
        try:
            with path.open("rb") as source_file:
                raw_content = source_file.read(MAX_FILE_BYTES + 1)
            if len(raw_content) > MAX_FILE_BYTES:
                excluded_keys.add((relative, "file-too-large"))
                omissions.append((relative, "file-too-large"))
                continue
            text = raw_content.decode("utf-8")
        except (OSError, UnicodeError):
            excluded_keys.add((relative, "unsupported-or-unreadable-content"))
            omissions.append((relative, "unsupported-or-unreadable-content"))
            continue
        lines = text.splitlines()
        start, end = _line_bounds(requested_lines)
        if not lines or start > len(lines):
            gaps.append({
                "code": "missing-source-lines",
                "detail": "Observed source lines are not present in the selected file.",
                "observationId": sorted(set(observation_ids))[0],
                "source": {"path": relative, "lines": requested_lines},
            })
            continue
        end = min(end, len(lines))
        if is_config_path(relative):
            lines = mask_config_text("\n".join(lines), relative).split("\n")
        else:
            lines = mask_account_ids("\n".join(lines)).split("\n")
        content, actual_end, truncated = _bounded_content(lines, start, end)
        truncated = truncated or ((relative, requested_lines) in whole_file_keys and actual_end < len(lines))
        content_bytes = len(content.encode("utf-8"))
        if total_bytes + content_bytes > MAX_TOTAL_CONTENT_BYTES:
            excluded_keys.add((relative, "selection-total-content-limit"))
            omissions.append((relative, "selection-total-content-limit"))
            continue
        actual_lines = str(start) if actual_end == start else "{}-{}".format(start, actual_end)
        selected.append({
            "path": relative,
            "lines": actual_lines,
            "content": content,
            "contentSha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "observationIds": sorted(set(observation_ids)),
        })
        selected_paths.add(relative)
        selected_candidate_keys.add((relative, requested_lines))
        ranges_by_path[relative] = ranges_by_path.get(relative, 0) + 1
        total_bytes += content_bytes
        if truncated:
            gaps.append({
                "code": "content-truncated",
                "detail": "Selected content exceeded the per-selection line or byte limit.",
                "observationId": sorted(set(observation_ids))[0],
                "source": {"path": relative, "lines": requested_lines},
            })

    bundle = {
        "schemaVersion": 1,
        "repository": inventory["repository"],
        "kind": _bundle_kind(observations, inventory.get("sourceSelection")),
        "commit": inventory["commit"],
        "inventorySha256": inventory_digest(inventory),
        "selectedEvidence": sorted(selected, key=lambda item: (item["path"], item["lines"])),
        "selectionCoverage": _selection_coverage(candidates, selected, omissions, selected_candidate_keys),
        "gaps": sorted(gaps, key=lambda item: (
            item["source"]["path"], item["source"]["lines"], item["code"]
        )),
        "excluded": [
            {"path": path, "reason": reason} for path, reason in sorted(excluded_keys)
        ],
    }
    if purpose == "semantic":
        bundle["selectionPurpose"] = purpose
    if "sourceSelection" in inventory:
        bundle["sourceSelection"] = dict(inventory["sourceSelection"])
    bundle["id"] = artifact_id(bundle)
    bundle_errors = validate_evidence_bundle(bundle)
    if bundle_errors:
        raise ValueError("Generated evidence bundle is invalid: {}".format("; ".join(bundle_errors)))
    return bundle
