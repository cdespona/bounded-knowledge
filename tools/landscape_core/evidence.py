"""Deterministic, bounded evidence selection from a validated inventory."""

import hashlib
import json
from pathlib import Path, PurePosixPath

from .contracts import artifact_id, validate_evidence_bundle
from .git_repository import commit_sha, dirty_paths, require_repository_root
from .safety import MAX_FILE_BYTES, exclusion_reason
from .validation import validate_inventory


MAX_SELECTED_FILES = 64
MAX_RANGES_PER_FILE = 16
MAX_LINES_PER_SELECTION = 200
MAX_CONTENT_BYTES = 16 * 1024
MAX_TOTAL_CONTENT_BYTES = 256 * 1024
GAP_KINDS = {"api-gap", "manifest-gap"}


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


def _bundle_kind(observations):
    return "application" if any(
        item.get("kind") in {"build-project", "declared-dependency", "build-plugin", "build-module"}
        for item in observations
        if isinstance(item, dict)
    ) else "other"


def select_evidence(inventory, source):
    """Return a validated evidence bundle without modifying the source repository."""
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
    precise_paths = {
        item["source"]["path"]
        for item in observations
        if isinstance(item, dict)
        and isinstance(item.get("source"), dict)
        and item["source"].get("lines")
        and item.get("kind") not in GAP_KINDS
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
        source_lines = source_ref.get("lines")
        if source_lines is None and relative in precise_paths:
            continue
        key = (relative, source_lines or "1-200")
        candidates.setdefault(key, []).append(item["id"])
        if source_lines is None:
            whole_file_keys.add(key)

    # This is the final HEAD check before any selected source content is read.
    if commit_sha(root) != inventory["commit"]:
        raise ValueError("Inventory commit does not match the source repository HEAD")

    selected = []
    excluded = list(inventory["excluded"])
    excluded_keys = {(item["path"], item["reason"]) for item in excluded}
    selected_paths = set()
    ranges_by_path = {}
    total_bytes = 0
    for (relative, requested_lines), observation_ids in sorted(candidates.items()):
        relative_path = Path(relative)
        path = root / relative_path
        reason = _inventory_exclusion_reason(relative, inventory["excluded"])
        if reason is None:
            reason = exclusion_reason(relative_path)
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
            continue
        if relative not in selected_paths and len(selected_paths) >= MAX_SELECTED_FILES:
            excluded_keys.add((relative, "selection-file-limit"))
            continue
        if ranges_by_path.get(relative, 0) >= MAX_RANGES_PER_FILE:
            excluded_keys.add((relative, "selection-range-limit"))
            continue
        try:
            with path.open("rb") as source_file:
                raw_content = source_file.read(MAX_FILE_BYTES + 1)
            if len(raw_content) > MAX_FILE_BYTES:
                excluded_keys.add((relative, "file-too-large"))
                continue
            text = raw_content.decode("utf-8")
        except (OSError, UnicodeError):
            excluded_keys.add((relative, "unsupported-or-unreadable-content"))
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
        content, actual_end, truncated = _bounded_content(lines, start, end)
        truncated = truncated or ((relative, requested_lines) in whole_file_keys and actual_end < len(lines))
        content_bytes = len(content.encode("utf-8"))
        if total_bytes + content_bytes > MAX_TOTAL_CONTENT_BYTES:
            excluded_keys.add((relative, "selection-total-content-limit"))
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
        "kind": _bundle_kind(observations),
        "commit": inventory["commit"],
        "inventorySha256": inventory_digest(inventory),
        "selectedEvidence": sorted(selected, key=lambda item: (item["path"], item["lines"])),
        "gaps": sorted(gaps, key=lambda item: (
            item["source"]["path"], item["source"]["lines"], item["code"]
        )),
        "excluded": [
            {"path": path, "reason": reason} for path, reason in sorted(excluded_keys)
        ],
    }
    bundle["id"] = artifact_id(bundle)
    bundle_errors = validate_evidence_bundle(bundle)
    if bundle_errors:
        raise ValueError("Generated evidence bundle is invalid: {}".format("; ".join(bundle_errors)))
    return bundle
