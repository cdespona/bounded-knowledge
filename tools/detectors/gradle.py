"""Conservative literal Gradle extraction without evaluating build scripts."""

import re
from pathlib import PurePosixPath

from landscape_core.observations import observation
from landscape_core.safety import MAX_FILE_BYTES

from .manifest_files import manifest_files


QUOTED = r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)"
DEPENDENCY = re.compile(
    r"^(?P<scope>[A-Za-z_][A-Za-z0-9_]*)\s*\(\s*" + QUOTED + r"\s*\)\s*;?$"
)
DEPENDENCY_GROOVY = re.compile(
    r"^(?P<scope>[A-Za-z_][A-Za-z0-9_]*)\s+" + QUOTED + r"\s*;?$"
)
PLUGIN_ID = re.compile(
    r"^id\s*\(\s*(['\"])(?P<plugin>.*?)\1\s*\)\s*(?:version\s*(['\"])(?P<version>.*?)\3)?\s*;?$"
)
PLUGIN_ID_GROOVY = re.compile(
    r"^id\s+(['\"])(?P<plugin>.*?)\1\s*(?:version\s+(['\"])(?P<version>.*?)\3)?\s*;?$"
)
KOTLIN_PLUGIN = re.compile(
    r"^kotlin\s*\(\s*(['\"])(?P<plugin>.*?)\1\s*\)\s*(?:version\s*(['\"])(?P<version>.*?)\3)?\s*;?$"
)
ROOT_NAME = re.compile(r"^rootProject\.name\s*=\s*(['\"])(?P<value>.*?)\1\s*;?$")
INCLUDE = re.compile(r"^include\s*\((?P<arguments>.*)\)\s*;?$")
INCLUDE_GROOVY = re.compile(r"^include\s+(?P<arguments>.+?)\s*;?$")
STRING = re.compile(r"\s*(['\"])(.*?)\1\s*(?:,|$)")


def _property_reference(value):
    return "$" in value


def _module_path(value):
    normalized = value.strip(":").replace(":", "/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


class GradleManifestDetector:
    name = "gradle-manifest"
    version = 1

    def _item(self, root, path, repository, commit, line, kind, value):
        return observation(
            kind=kind,
            repository=repository,
            commit=commit,
            detector=self.name,
            detector_version=self.version,
            source_path=path.relative_to(root).as_posix(),
            source_lines=str(line),
            value=value,
        )

    def _gap(self, root, path, repository, commit, line, code, detail):
        return self._item(
            root, path, repository, commit, line, "manifest-gap",
            {"buildSystem": "gradle", "code": code, "detail": detail},
        )

    def _extract_plugins(self, root, path, repository, commit, lines):
        observations = []
        depth = 0
        in_plugins = False
        for number, raw in enumerate(lines, 1):
            text = raw.split("//", 1)[0].strip()
            if not in_plugins and re.match(r"^plugins\s*\{", text):
                in_plugins = True
                depth = text.count("{") - text.count("}")
                continue
            if not in_plugins:
                continue
            depth += text.count("{") - text.count("}")
            if depth <= 0:
                in_plugins = False
                continue
            if not text:
                continue
            match = PLUGIN_ID.match(text) or PLUGIN_ID_GROOVY.match(text) or KOTLIN_PLUGIN.match(text)
            if match:
                plugin = match.group("plugin")
                if match.re is KOTLIN_PLUGIN:
                    plugin = "org.jetbrains.kotlin.{}".format(plugin)
                version = match.groupdict().get("version")
                values = [plugin] + ([version] if version else [])
                value = {
                    "buildSystem": "gradle",
                    "plugin": plugin,
                    "declaration": "property-reference" if any(_property_reference(item) for item in values) else "literal",
                }
                if version:
                    value["version"] = version
                observations.append(self._item(root, path, repository, commit, number, "build-plugin", value))
            elif re.fullmatch(r"(?:java|java-library|application|groovy|scala|maven-publish)\s*;?", text):
                observations.append(self._item(
                    root, path, repository, commit, number, "build-plugin",
                    {"buildSystem": "gradle", "plugin": text.rstrip(";"), "declaration": "literal"},
                ))
            else:
                observations.append(self._gap(
                    root, path, repository, commit, number, "dynamic-plugin",
                    "Plugin declaration is not a supported literal form.",
                ))
        return observations

    def _extract_dependencies(self, root, path, repository, commit, lines):
        observations = []
        depth = 0
        in_dependencies = False
        for number, raw in enumerate(lines, 1):
            text = raw.split("//", 1)[0].strip()
            if not in_dependencies and re.match(r"^dependencies\s*\{", text):
                in_dependencies = True
                depth = text.count("{") - text.count("}")
                continue
            if not in_dependencies:
                continue
            depth += text.count("{") - text.count("}")
            if depth <= 0:
                in_dependencies = False
                continue
            if not text or text in {"{", "}"}:
                continue
            if depth != 1:
                observations.append(self._gap(
                    root, path, repository, commit, number, "dynamic-dependency",
                    "Nested dependency constructs are not interpreted as direct declarations.",
                ))
                continue
            match = DEPENDENCY.match(text) or DEPENDENCY_GROOVY.match(text)
            if match:
                coordinate = match.group("value")
                parts = coordinate.split(":")
                if len(parts) in (2, 3) and all(parts):
                    value = {
                        "buildSystem": "gradle",
                        "group": parts[0],
                        "artifact": parts[1],
                        "scope": match.group("scope"),
                        "declaration": "property-reference" if _property_reference(coordinate) else "literal",
                    }
                    if len(parts) == 3:
                        value["version"] = parts[2]
                    observations.append(self._item(root, path, repository, commit, number, "declared-dependency", value))
                    if len(parts) == 2:
                        observations.append(self._gap(
                            root, path, repository, commit, number, "version-not-literal",
                            "Dependency version is omitted and may be supplied by an evaluated Gradle model.",
                        ))
                else:
                    observations.append(self._gap(
                        root, path, repository, commit, number, "unsupported-coordinate",
                        "Dependency string is not group:artifact[:version].",
                    ))
            else:
                observations.append(self._gap(
                    root, path, repository, commit, number, "dynamic-dependency",
                    "Dependency declaration is not a supported literal string form.",
                ))
        return observations

    def _extract_settings(self, root, path, repository, commit, lines):
        observations = []
        for number, raw in enumerate(lines, 1):
            text = raw.split("//", 1)[0].strip()
            if not text:
                continue
            root_name = ROOT_NAME.match(text)
            if root_name:
                observations.append(self._item(
                    root, path, repository, commit, number, "build-project",
                    {"buildSystem": "gradle", "artifact": root_name.group("value")},
                ))
                continue
            include = INCLUDE.match(text) or INCLUDE_GROOVY.match(text)
            if include:
                arguments = include.group("arguments")
                position = 0
                values = []
                while position < len(arguments):
                    match = STRING.match(arguments, position)
                    if not match:
                        values = []
                        break
                    values.append(match.group(2))
                    position = match.end()
                if values:
                    for value in values:
                        module = _module_path(value)
                        if module and not _property_reference(value):
                            observations.append(self._item(
                                root, path, repository, commit, number, "build-module",
                                {"buildSystem": "gradle", "path": module},
                            ))
                        else:
                            observations.append(self._gap(
                                root, path, repository, commit, number, "dynamic-module",
                                "Module path is dynamic or unsafe and was not interpreted.",
                            ))
                else:
                    observations.append(self._gap(
                        root, path, repository, commit, number, "dynamic-module",
                        "Include arguments are not supported literal strings.",
                    ))
            elif text.startswith("includeBuild") or text.startswith("pluginManagement") or text.startswith("dependencyResolutionManagement"):
                observations.append(self._gap(
                    root, path, repository, commit, number, "unsupported-settings",
                    "Settings construct is not evaluated by literal extraction.",
                ))
            elif text not in {"{", "}"}:
                observations.append(self._gap(
                    root, path, repository, commit, number, "unsupported-settings-syntax",
                    "Settings syntax is not a supported literal project or module declaration.",
                ))
        return observations

    def _extract(self, root, path, repository, commit):
        try:
            with path.open("rb") as source:
                content = source.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise ValueError("manifest exceeds the source file size limit")
            lines = content.decode("utf-8").splitlines()
        except (OSError, UnicodeError, ValueError) as error:
            return [self._gap(
                root, path, repository, commit, 1, "unsupported-manifest-content",
                "Gradle manifest could not be read safely: {}.".format(str(error)),
            )]
        if path.name.startswith("settings.gradle"):
            observations = self._extract_settings(root, path, repository, commit, lines)
            if observations:
                return observations
            return [self._gap(
                root, path, repository, commit, 1, "unsupported-gradle-syntax",
                "No supported literal Gradle settings declaration was recognized.",
            )]
        observations = self._extract_plugins(root, path, repository, commit, lines)
        observations.extend(self._extract_dependencies(root, path, repository, commit, lines))
        for number, raw in enumerate(lines, 1):
            text = raw.split("//", 1)[0].strip()
            if re.match(r"^(?:buildscript|subprojects|allprojects)\s*\{", text):
                observations.append(self._gap(
                    root, path, repository, commit, number, "dynamic-build-scope",
                    "Build scope can alter declarations and is not evaluated.",
                ))
        if not observations:
            first = next(
                (number for number, raw in enumerate(lines, 1) if raw.strip() and not raw.strip().startswith("//")),
                1,
            )
            observations.append(self._gap(
                root, path, repository, commit, first, "unsupported-gradle-syntax",
                "No supported literal Gradle declaration was recognized.",
            ))
        return observations

    def detect(self, root, repository, commit):
        observations = []
        names = {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}
        for path in manifest_files(root, names):
            observations.extend(self._extract(root, path, repository, commit))
        return observations, []
