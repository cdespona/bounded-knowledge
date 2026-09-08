"""Load and resolve approved, read-only source repositories."""

import hashlib
import json
from pathlib import Path, PurePosixPath

from .contracts import validate_source_registry, validate_source_resolution
from .git_repository import commit_sha, dirty_paths, require_repository_root
from .safety import (
    COPILOT_CONFIGURATION_DIRECTORIES,
    MAX_FILE_BYTES,
    copilot_configuration_files,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SourceRegistryError(ValueError):
    """Raised when a source registry or configured repository is unsafe."""


def load_source_registry(path):
    registry_path = Path(path)
    document = json.loads(registry_path.read_text(encoding="utf-8"))
    errors = validate_source_registry(document)
    if errors:
        raise SourceRegistryError(
            "Invalid source registry: {}".format("; ".join(errors))
        )
    return document


def _canonical_registry_paths(registry):
    canonical = {}
    for item in registry["repositories"]:
        try:
            resolved = Path(item["path"]).resolve(strict=False)
        except (OSError, RuntimeError) as error:
            raise SourceRegistryError(
                "Cannot resolve configured path for {}: {}".format(item["id"], error)
            )
        previous = canonical.get(resolved)
        if previous is not None:
            raise SourceRegistryError(
                "Repositories {} and {} resolve to the same path: {}".format(
                    previous, item["id"], resolved
                )
            )
        canonical[resolved] = item["id"]
    return canonical


def _configuration_path(root, relative):
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise SourceRegistryError(
                "Copilot configuration must not use symbolic links: {}".format(relative)
            )
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise SourceRegistryError(
            "Copilot configuration escapes the repository: {} ({})".format(relative, error)
        )
    if not resolved.is_file():
        raise SourceRegistryError(
            "Copilot configuration is not a regular file: {}".format(relative)
        )
    size = resolved.stat().st_size
    if size > MAX_FILE_BYTES:
        raise SourceRegistryError(
            "Copilot configuration exceeds the file-size limit: {}".format(relative)
        )
    return resolved


def copilot_configuration_digest(root, relative_paths):
    """Hash configuration paths and content without executing or interpreting them."""
    digest = hashlib.sha256()
    for relative in relative_paths:
        path = _configuration_path(root, relative)
        content = path.read_bytes()
        if (
            relative == ".github/copilot-instructions.md"
            or path.name in {"AGENTS.md", "CLAUDE.md"}
        ) and b"@" in content:
            raise SourceRegistryError(
                "Copilot instruction file references are not yet supported safely: {}".format(
                    relative
                )
            )
        content_digest = hashlib.sha256(content).digest()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content_digest)
        digest.update(b"\0")
    return digest.hexdigest()


def _reject_symlinked_configuration_roots(root):
    candidates = (
        root / "AGENTS.md",
        root / "CLAUDE.md",
        root / ".github" / "copilot-instructions.md",
        *(root / relative for relative in COPILOT_CONFIGURATION_DIRECTORIES),
    )
    for candidate in candidates:
        if candidate.is_symlink():
            raise SourceRegistryError(
                "Copilot configuration must not use symbolic links: {}".format(
                    candidate.relative_to(root).as_posix()
                )
            )
        if candidate.is_dir():
            for nested in candidate.rglob("*"):
                if nested.is_symlink():
                    raise SourceRegistryError(
                        "Copilot configuration must not use symbolic links: {}".format(
                            nested.relative_to(root).as_posix()
                        )
                    )


def resolve_source(registry_path, repository_id, catalog_root=PROJECT_ROOT):
    registry = load_source_registry(registry_path)
    _canonical_registry_paths(registry)
    matches = [
        item for item in registry["repositories"] if item["id"] == repository_id
    ]
    if not matches:
        raise SourceRegistryError(
            "Repository is not registered: {}".format(repository_id)
        )
    source = matches[0]
    if not source["enabled"]:
        raise SourceRegistryError("Repository is disabled: {}".format(repository_id))

    configured = Path(source["path"])
    if configured.is_symlink():
        raise SourceRegistryError(
            "Repository root must not be a symbolic link: {}".format(configured)
        )
    try:
        resolved = configured.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise SourceRegistryError(
            "Cannot resolve repository {}: {}".format(repository_id, error)
        )
    if resolved == Path(catalog_root).resolve():
        raise SourceRegistryError(
            "The bounded-knowledge observatory cannot be registered as a source"
        )

    root = require_repository_root(resolved)
    dirty = dirty_paths(root)
    if dirty:
        raise SourceRegistryError(
            "Repository must have a clean working tree: {}".format(", ".join(dirty))
        )

    _reject_symlinked_configuration_roots(root)
    configuration_files = copilot_configuration_files(root)
    configuration_digest = copilot_configuration_digest(root, configuration_files)
    approved_digest = source.get("approvedCopilotConfigurationSha256")
    access_approved = not configuration_files or approved_digest == configuration_digest
    result = {
        "schemaVersion": 1,
        "repository": source["id"],
        "kind": source["kind"],
        "configuredPath": str(configured),
        "resolvedPath": str(root),
        "commit": commit_sha(root),
        "exclusions": sorted(source.get("exclude", [])),
        "copilotConfigurationFiles": configuration_files,
        "copilotConfigurationSha256": configuration_digest,
        "copilotAccessApproved": access_approved,
    }
    errors = validate_source_resolution(result)
    if errors:
        raise SourceRegistryError(
            "Resolved source violates its contract: {}".format("; ".join(errors))
        )
    return result
