"""Technology-neutral repository file inventory."""

import os
from pathlib import Path

from landscape_core.observations import observation
from landscape_core.safety import directory_exclusion_reason, exclusion_reason


MANIFEST_NAMES = {
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle-kotlin",
    "settings.gradle": "gradle-settings",
    "settings.gradle.kts": "gradle-kotlin-settings",
    "pom.xml": "maven",
    "package.json": "npm",
    "Dockerfile": "docker",
    "Chart.yaml": "helm",
    "kustomization.yaml": "kustomize",
    "kustomization.yml": "kustomize",
}

KNOWN_EXTENSIONS = {
    ".java": "java-source",
    ".kt": "kotlin-source",
    ".kts": "kotlin-script",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".properties": "properties",
    ".md": "documentation",
    ".tf": "terraform",
    ".sql": "sql",
}


class GenericFileDetector:
    name = "generic-files"
    version = 1

    def detect(self, root, repository, commit):
        observations = []
        excluded = []
        for current, directory_names, file_names in os.walk(root, topdown=True):
            current_path = Path(current)
            retained_directories = []
            for directory_name in sorted(directory_names):
                directory = current_path / directory_name
                relative = directory.relative_to(root).as_posix()
                if directory.is_symlink():
                    excluded.append({"path": relative, "reason": "symbolic-link"})
                elif directory_exclusion_reason(directory.relative_to(root)) is not None:
                    excluded.append({"path": relative, "reason": "excluded-directory"})
                else:
                    retained_directories.append(directory_name)
            directory_names[:] = retained_directories

            for file_name in sorted(file_names):
                path = current_path / file_name
                if path.is_symlink():
                    relative = path.relative_to(root).as_posix()
                    excluded.append({"path": relative, "reason": "symbolic-link"})
                    continue

                relative_path = path.relative_to(root)
                relative = relative_path.as_posix()
                try:
                    size = path.stat().st_size
                except OSError:
                    excluded.append({"path": relative, "reason": "unreadable-file"})
                    continue

                reason = exclusion_reason(relative_path, size)
                if reason:
                    excluded.append({"path": relative, "reason": reason})
                    continue

                manifest_type = MANIFEST_NAMES.get(path.name)
                category = manifest_type or KNOWN_EXTENSIONS.get(
                    path.suffix.lower(), "unsupported"
                )
                kind = "manifest-file" if manifest_type else "repository-file"
                observations.append(
                    observation(
                        kind=kind,
                        repository=repository,
                        commit=commit,
                        detector=self.name,
                        detector_version=self.version,
                        source_path=relative,
                        value={
                            "category": category,
                            "extension": path.suffix.lower(),
                            "sizeBytes": size,
                        },
                    )
                )
        return observations, excluded
