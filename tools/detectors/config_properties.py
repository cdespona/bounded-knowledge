"""Literal application configuration inventory for Java-properties and YAML files."""

import os
from pathlib import Path

from landscape_core.config_files import (
    classify_resource, describe_value, file_profile, is_config_path, parse_config,
)
from landscape_core.observations import observation
from landscape_core.safety import MAX_FILE_BYTES, directory_exclusion_reason, exclusion_reason


class ConfigPropertiesDetector:
    name = "config-properties"
    version = 1

    def _candidates(self, root):
        for current, directory_names, file_names in os.walk(root, topdown=True):
            current_path = Path(current)
            directory_names[:] = [
                name for name in sorted(directory_names)
                if not (current_path / name).is_symlink()
                and directory_exclusion_reason((current_path / name).relative_to(root)) is None
            ]
            for file_name in sorted(file_names):
                path = current_path / file_name
                relative = path.relative_to(root)
                if path.is_symlink() or not is_config_path(relative.as_posix()):
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if exclusion_reason(relative, size) is None and size <= MAX_FILE_BYTES:
                    yield path, relative.as_posix()

    def detect(self, root, repository, commit):
        observations = []
        for path, relative in self._candidates(root):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            entries, gaps = parse_config(text, relative)
            form = "properties" if relative.endswith(".properties") else "yaml"
            fallback = file_profile(relative)
            for entry in entries:
                disposition, value = describe_value(entry["key"], entry["value"])
                observations.append(observation(
                    kind="config-property", repository=repository, commit=commit,
                    detector=self.name, detector_version=self.version, source_path=relative,
                    value={"key": entry["key"], "profile": entry["profile"] or fallback, "form": form,
                           "line": entry["line"], "disposition": disposition, "value": value,
                           "resource": classify_resource(entry["key"], entry["value"])}))
            for gap in gaps:
                observations.append(observation(
                    kind="config-gap", repository=repository, commit=commit,
                    detector=self.name, detector_version=self.version, source_path=relative,
                    source_lines=str(gap["line"]),
                    value={"form": form, "code": gap["code"], "detail": gap["detail"]}))
        return observations, []
