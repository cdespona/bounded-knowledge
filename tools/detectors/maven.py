"""Conservative Maven POM extraction without invoking Maven."""

from pathlib import Path, PurePosixPath
from xml.parsers import expat

from landscape_core.observations import observation
from landscape_core.safety import MAX_FILE_BYTES

from .manifest_files import manifest_files


class _Node:
    def __init__(self, name, start):
        self.name = name.split(":")[-1]
        self.start = start
        self.end = start
        self.text = []
        self.children = []

    def child(self, name):
        return next((item for item in self.children if item.name == name), None)

    def children_named(self, name):
        return [item for item in self.children if item.name == name]

    def value(self):
        return "".join(self.text).strip()


def _parse_xml(content):
    parser = expat.ParserCreate(namespace_separator=":")
    stack = []
    roots = []

    def start(name, _attributes):
        node = _Node(name, parser.CurrentLineNumber)
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)

    def end(_name):
        node = stack.pop()
        node.end = parser.CurrentLineNumber

    def text(value):
        if stack:
            stack[-1].text.append(value)

    def reject_doctype(*_args):
        raise ValueError("DOCTYPE declarations are not supported")

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text
    parser.StartDoctypeDeclHandler = reject_doctype
    parser.Parse(content, True)
    if len(roots) != 1:
        raise ValueError("expected exactly one XML root element")
    return roots[0]


def _lines(node):
    return str(node.start) if node.start == node.end else "{}-{}".format(node.start, node.end)


def _is_property_reference(value):
    return "${" in value


def _declaration(values):
    return "property-reference" if any(_is_property_reference(value) for value in values) else "literal"


class MavenManifestDetector:
    name = "maven-manifest"
    version = 1

    def _observation(self, *, root, path, repository, commit, kind, value, node):
        return observation(
            kind=kind,
            repository=repository,
            commit=commit,
            detector=self.name,
            detector_version=self.version,
            source_path=path.relative_to(root).as_posix(),
            source_lines=_lines(node),
            value=value,
        )

    def _gap(self, root, path, repository, commit, node, code, detail):
        return self._observation(
            root=root,
            path=path,
            repository=repository,
            commit=commit,
            kind="manifest-gap",
            node=node,
            value={"buildSystem": "maven", "code": code, "detail": detail},
        )

    def _extract(self, root, path, repository, commit):
        relative = path.relative_to(root).as_posix()
        try:
            with path.open("rb") as source:
                content = source.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                raise ValueError("manifest exceeds the source file size limit")
            project = _parse_xml(content)
        except (OSError, ValueError, expat.ExpatError, UnicodeError) as error:
            fallback = _Node("project", 1)
            return [self._gap(
                root, path, repository, commit, fallback, "unsupported-xml",
                "The Maven manifest could not be parsed safely: {}.".format(str(error)),
            )]

        if project.name != "project":
            return [self._gap(
                root, path, repository, commit, project, "unsupported-root",
                "Expected a Maven project root element in {}.".format(relative),
            )]

        observations = []
        artifact = project.child("artifactId")
        if artifact and artifact.value():
            value = {"buildSystem": "maven", "artifact": artifact.value()}
            coordinates = [artifact]
            for xml_name, field in (
                ("groupId", "group"), ("version", "version"), ("packaging", "packaging")
            ):
                node = project.child(xml_name)
                if node and node.value():
                    value[field] = node.value()
                    coordinates.append(node)
            source = _Node("coordinates", min(node.start for node in coordinates))
            source.end = max(node.end for node in coordinates)
            observations.append(self._observation(
                root=root, path=path, repository=repository, commit=commit,
                kind="build-project", value=value, node=source,
            ))
        else:
            observations.append(self._gap(
                root, path, repository, commit, project, "missing-project-artifact",
                "The project has no literal artifactId declaration.",
            ))

        parent = project.child("parent")
        if parent is not None:
            observations.append(self._gap(
                root, path, repository, commit, parent, "inherited-effective-model",
                "Parent POM inheritance is recorded but not evaluated.",
            ))
        for profile in (project.child("profiles").children_named("profile") if project.child("profiles") else []):
            observations.append(self._gap(
                root, path, repository, commit, profile, "profile-dependent",
                "Maven profile content is not included in literal project observations.",
            ))

        dependencies = project.child("dependencies")
        if dependencies:
            for dependency in dependencies.children_named("dependency"):
                fields = {name: dependency.child(name) for name in ("groupId", "artifactId", "version", "scope")}
                missing = [name for name in ("groupId", "artifactId") if not fields[name] or not fields[name].value()]
                if missing:
                    observations.append(self._gap(
                        root, path, repository, commit, dependency, "incomplete-dependency",
                        "Dependency lacks literal required fields: {}.".format(", ".join(missing)),
                    ))
                    continue
                values = [fields[name].value() for name in fields if fields[name] and fields[name].value()]
                value = {
                    "buildSystem": "maven",
                    "group": fields["groupId"].value(),
                    "artifact": fields["artifactId"].value(),
                    "scope": fields["scope"].value() if fields["scope"] and fields["scope"].value() else "unspecified",
                    "declaration": _declaration(values),
                }
                if fields["version"] and fields["version"].value():
                    value["version"] = fields["version"].value()
                observations.append(self._observation(
                    root=root, path=path, repository=repository, commit=commit,
                    kind="declared-dependency", value=value, node=dependency,
                ))
                if not fields["scope"] or not fields["scope"].value():
                    observations.append(self._gap(
                        root, path, repository, commit, dependency, "inherited-default-scope",
                        "Dependency scope is omitted; Maven's effective default is not applied.",
                    ))
                if not fields["version"] or not fields["version"].value():
                    observations.append(self._gap(
                        root, path, repository, commit, dependency, "inherited-dependency-version",
                        "Dependency version is omitted and may come from the effective model.",
                    ))
                supported = {"groupId", "artifactId", "version", "scope"}
                extras = sorted({node.name for node in dependency.children} - supported)
                if extras:
                    observations.append(self._gap(
                        root, path, repository, commit, dependency, "unsupported-dependency-fields",
                        "Dependency fields are not represented: {}.".format(", ".join(extras)),
                    ))

        management = project.child("dependencyManagement")
        if management is not None:
            observations.append(self._gap(
                root, path, repository, commit, management, "dependency-management",
                "Dependency management affects the effective model and is not evaluated.",
            ))

        build = project.child("build")
        plugins = build.child("plugins") if build else None
        if plugins:
            for plugin in plugins.children_named("plugin"):
                group = plugin.child("groupId")
                artifact_node = plugin.child("artifactId")
                version = plugin.child("version")
                if not artifact_node or not artifact_node.value():
                    observations.append(self._gap(
                        root, path, repository, commit, plugin, "incomplete-plugin",
                        "Plugin requires a literal artifactId declaration.",
                    ))
                    continue
                group_value = group.value() if group and group.value() else None
                values = ([group_value] if group_value else []) + [artifact_node.value()]
                value = {
                    "buildSystem": "maven",
                    "plugin": "{}:{}".format(group_value, artifact_node.value()) if group_value else artifact_node.value(),
                    "declaration": _declaration(values + ([version.value()] if version and version.value() else [])),
                }
                if version and version.value():
                    value["version"] = version.value()
                observations.append(self._observation(
                    root=root, path=path, repository=repository, commit=commit,
                    kind="build-plugin", value=value, node=plugin,
                ))
                if not group_value:
                    observations.append(self._gap(
                        root, path, repository, commit, plugin, "inherited-plugin-group",
                        "Plugin groupId is omitted; Maven's effective default is not applied.",
                    ))
                if not version or not version.value():
                    observations.append(self._gap(
                        root, path, repository, commit, plugin, "inherited-plugin-version",
                        "Plugin version is omitted and may come from the effective model.",
                    ))

        plugin_management = build.child("pluginManagement") if build else None
        if plugin_management is not None:
            observations.append(self._gap(
                root, path, repository, commit, plugin_management, "plugin-management",
                "Plugin management affects the effective model and is not evaluated.",
            ))

        modules = project.child("modules")
        if modules:
            for module in modules.children_named("module"):
                value = module.value()
                if not value or PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts:
                    observations.append(self._gap(
                        root, path, repository, commit, module, "unsafe-module-path",
                        "Module path is empty, absolute, or contains traversal.",
                    ))
                else:
                    observations.append(self._observation(
                        root=root, path=path, repository=repository, commit=commit,
                        kind="build-module", value={"buildSystem": "maven", "path": value}, node=module,
                    ))
                    if _is_property_reference(value):
                        observations.append(self._gap(
                            root, path, repository, commit, module, "property-reference",
                            "Module property reference is preserved and not evaluated.",
                        ))
        for name in (
            "repositories", "pluginRepositories", "reporting", "distributionManagement"
        ):
            node = project.child(name)
            if node is not None:
                observations.append(self._gap(
                    root, path, repository, commit, node, "unsupported-maven-section",
                    "Maven section '{}' is not represented by Slice 3 observations.".format(name),
                ))
        return observations

    def detect(self, root, repository, commit):
        observations = []
        for path in manifest_files(root, {"pom.xml"}):
            observations.extend(self._extract(root, path, repository, commit))
        return observations, []
