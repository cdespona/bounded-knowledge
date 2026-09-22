"""Deterministic repository discovery orchestration."""

from pathlib import Path

from detectors import built_in_detectors
from .contracts import validate_source_topology
from .git_repository import commit_sha, dirty_paths, require_repository_root
from .safety import copilot_configuration_files


def preflight(path: Path):
    root = require_repository_root(path)
    dirty = dirty_paths(root)
    return {
        "root": str(root),
        "commit": commit_sha(root),
        "clean": not dirty,
        "dirtyPaths": dirty,
        "copilotConfigurationFiles": copilot_configuration_files(root),
    }


def _resolve_selection(root, repository, topology, selection):
    if (topology is None) != (selection is None):
        raise ValueError("--topology and --selection must be provided together")
    if topology is None:
        return None
    errors = validate_source_topology(topology)
    if errors:
        raise ValueError("Invalid source topology: {}".format("; ".join(errors)))
    selected = next(
        (item for item in topology["sourceSelections"] if item["id"] == selection),
        None,
    )
    if selected is None:
        raise ValueError("Source selection is not present in the topology")
    topology_repository = next(
        (item for item in topology["repositories"] if item["id"] == repository),
        None,
    )
    if topology_repository is None:
        raise ValueError("Discovery repository is not present in the topology")
    if selected["repositoryId"] != repository:
        raise ValueError("Source selection belongs to another repository")
    if selected["kind"] != "kubernetes" or topology_repository["kind"] != "kubernetes":
        raise ValueError("Source selection and repository must both have kubernetes kind")
    current = root
    for part in Path(selected["subpath"]).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Source selection must not contain symbolic links")
    try:
        current.resolve().relative_to(root)
    except ValueError:
        raise ValueError("Source selection escapes the repository root")
    if not current.is_dir():
        raise ValueError("Source selection subpath must be an existing directory")
    return dict(selected)


def discover(path: Path, repository: str, topology=None, selection=None):
    check = preflight(path)
    if not check["clean"]:
        raise ValueError("Repository must have a clean working tree before discovery")

    root = Path(check["root"])
    commit = check["commit"]
    source_selection = _resolve_selection(
        root, repository, topology, selection
    )
    observations = []
    excluded = []
    detectors = built_in_detectors(source_selection)
    for detector in detectors:
        found, skipped = detector.detect(root, repository, commit)
        observations.extend(found)
        excluded.extend(skipped)

    result = {
        "schemaVersion": 1,
        "repository": repository,
        "commit": commit,
        "detectors": [
            {"name": detector.name, "version": detector.version} for detector in detectors
        ],
        "observations": sorted(observations, key=lambda item: item["id"]),
        "excluded": sorted(excluded, key=lambda item: (item["path"], item["reason"])),
    }
    if source_selection is not None:
        result["sourceSelection"] = source_selection
    return result
