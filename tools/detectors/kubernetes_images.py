"""Bounded literal container images from one reviewed Kubernetes YAML selection."""

import os
from pathlib import Path

import yaml
from yaml.events import (
    AliasEvent,
    CollectionEndEvent,
    CollectionStartEvent,
    DocumentStartEvent,
    NodeEvent,
)
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from landscape_core.contracts import KUBERNETES_GAP_DETAILS
from landscape_core.observations import observation
from landscape_core.safety import (
    MAX_FILE_BYTES,
    directory_exclusion_reason,
    exclusion_reason,
)
from .container_images import IMAGE_NAME


MAX_DOCUMENTS = 64
MAX_DEPTH = 64
MAX_NODES = 50_000
MAX_RESOURCES = 1_024
MAX_CONTAINERS = 4_096
MAX_LIST_DEPTH = 8

SUPPORTED_WORKLOADS = {
    ("v1", "Pod"): ("spec",),
    ("apps/v1", "Deployment"): ("spec", "template", "spec"),
    ("apps/v1", "StatefulSet"): ("spec", "template", "spec"),
    ("apps/v1", "DaemonSet"): ("spec", "template", "spec"),
    ("apps/v1", "ReplicaSet"): ("spec", "template", "spec"),
    ("batch/v1", "Job"): ("spec", "template", "spec"),
    ("batch/v1", "CronJob"): (
        "spec", "jobTemplate", "spec", "template", "spec"
    ),
}
TYPED_LISTS = {
    (version, kind + "List"): (version, kind)
    for version, kind in SUPPORTED_WORKLOADS
}
KNOWN_NON_IMAGE_KINDS = {
    "HorizontalPodAutoscaler",
    "NetworkPolicy",
    "PodDisruptionBudget",
    "ScaledObject",
    "Service",
    "ServiceAccount",
}
CONTAINER_CATEGORIES = ("containers", "initContainers", "ephemeralContainers")
DYNAMIC_MARKERS = ("$", "{{", "}}", "{%", "%}", "<%", "%>")
ALLOWED_YAML_TAGS = {
    "tag:yaml.org,2002:map",
    "tag:yaml.org,2002:seq",
    "tag:yaml.org,2002:str",
    "tag:yaml.org,2002:null",
    "tag:yaml.org,2002:bool",
    "tag:yaml.org,2002:int",
    "tag:yaml.org,2002:float",
    "tag:yaml.org,2002:timestamp",
    "tag:yaml.org,2002:binary",
}


class _YamlFatal(Exception):
    def __init__(self, code, line=1):
        super().__init__(code)
        self.code = code
        self.line = max(1, line)


class _ResourceLimit(Exception):
    pass


def _pointer(base, *parts):
    result = base
    for part in parts:
        escaped = str(part).replace("~", "~0").replace("/", "~1")
        result += "/" + escaped
    return result


def _line(node):
    return node.start_mark.line + 1 if node is not None else 1


def _mapping(node):
    if not isinstance(node, MappingNode):
        return None
    return {key.value: (key, value) for key, value in node.value}


def _literal(node):
    if isinstance(node, ScalarNode) and node.tag == "tag:yaml.org,2002:str":
        return node.value
    return None


def _candidate_files(root, selection_root):
    found = []
    excluded = []
    for current, directory_names, file_names in os.walk(selection_root, topdown=True):
        current_path = Path(current)
        retained = []
        for name in sorted(directory_names):
            path = current_path / name
            relative = path.relative_to(root)
            reason = directory_exclusion_reason(relative)
            if path.is_symlink():
                excluded.append({"path": relative.as_posix(), "reason": "symbolic-link"})
            elif reason is not None:
                excluded.append({"path": relative.as_posix(), "reason": reason})
            else:
                retained.append(name)
        directory_names[:] = retained
        for name in sorted(file_names):
            if Path(name).suffix.lower() not in {".yaml", ".yml"}:
                continue
            path = current_path / name
            relative = path.relative_to(root)
            if path.is_symlink():
                excluded.append({"path": relative.as_posix(), "reason": "symbolic-link"})
                continue
            try:
                size = path.stat().st_size
            except OSError:
                excluded.append({"path": relative.as_posix(), "reason": "unreadable-file"})
                continue
            reason = exclusion_reason(relative, size)
            if reason is not None:
                excluded.append({"path": relative.as_posix(), "reason": reason})
            else:
                found.append(path)
    found.sort(key=lambda path: path.relative_to(root).as_posix())
    excluded.sort(key=lambda item: (item["path"], item["reason"]))
    return found, excluded


def _preflight_events(text):
    depth = 0
    nodes = 0
    documents = 0
    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, DocumentStartEvent):
                documents += 1
                if documents > MAX_DOCUMENTS:
                    raise _YamlFatal("kubernetes-parser-resource-limit", event.start_mark.line + 1)
            if isinstance(event, AliasEvent):
                raise _YamlFatal("unsupported-kubernetes-alias", event.start_mark.line + 1)
            if isinstance(event, NodeEvent):
                nodes += 1
                if nodes > MAX_NODES:
                    raise _YamlFatal("kubernetes-parser-resource-limit", event.start_mark.line + 1)
                if getattr(event, "anchor", None) is not None:
                    raise _YamlFatal("unsupported-kubernetes-anchor", event.start_mark.line + 1)
                tag = getattr(event, "tag", None)
                if tag is not None and tag not in ALLOWED_YAML_TAGS:
                    raise _YamlFatal("unsupported-kubernetes-tag", event.start_mark.line + 1)
            if isinstance(event, CollectionStartEvent):
                depth += 1
                if depth > MAX_DEPTH:
                    raise _YamlFatal("kubernetes-parser-resource-limit", event.start_mark.line + 1)
            elif isinstance(event, CollectionEndEvent):
                depth -= 1
    except _YamlFatal:
        raise
    except (yaml.YAMLError, RecursionError, MemoryError) as error:
        mark = getattr(error, "problem_mark", None)
        raise _YamlFatal(
            "malformed-kubernetes-yaml",
            mark.line + 1 if mark is not None else 1,
        )


def _validate_nodes(documents):
    count = 0

    def visit(node, depth):
        nonlocal count
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise _YamlFatal("kubernetes-parser-resource-limit", _line(node))
        if node.tag not in ALLOWED_YAML_TAGS:
            raise _YamlFatal("unsupported-kubernetes-tag", _line(node))
        if isinstance(node, MappingNode):
            seen = set()
            for key, value in node.value:
                if not isinstance(key, ScalarNode):
                    raise _YamlFatal("unsupported-kubernetes-key", _line(key))
                if key.tag == "tag:yaml.org,2002:merge" or key.value == "<<":
                    raise _YamlFatal("unsupported-kubernetes-merge", _line(key))
                identity = (key.tag, key.value)
                if identity in seen:
                    raise _YamlFatal("duplicate-kubernetes-key", _line(key))
                seen.add(identity)
                visit(key, depth + 1)
                visit(value, depth + 1)
        elif isinstance(node, SequenceNode):
            for value in node.value:
                visit(value, depth + 1)

    for document in documents:
        if document is not None:
            visit(document, 1)


class KubernetesImageDetector:
    name = "kubernetes-images"
    version = 1

    def __init__(self, source_selection):
        self.selection = dict(source_selection)

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

    def _gap(
        self, root, path, repository, commit, line, code, document_index=0,
        pointer="",
    ):
        return self._item(
            root, path, repository, commit, line, "container-gap",
            {
                "context": "kubernetes-workload",
                "form": "kubernetes-yaml",
                "code": code,
                "detail": KUBERNETES_GAP_DETAILS[code],
                "sourceSelectionId": self.selection["id"],
                "documentIndex": document_index,
                "pointer": pointer,
            },
        )

    def _image(
        self, root, path, repository, commit, line, image, api_version,
        workload_kind, category, document_index, pointer,
    ):
        return self._item(
            root, path, repository, commit, line, "container-image-reference",
            {
                "role": "workload-image",
                "form": "kubernetes-yaml",
                "image": image,
                "sourceSelectionId": self.selection["id"],
                "apiVersion": api_version,
                "workloadKind": workload_kind,
                "containerCategory": category,
                "documentIndex": document_index,
                "pointer": pointer,
            },
        )

    def _pod_spec(
        self, root, path, repository, commit, node, api_version, workload_kind,
        document_index, pointer, state,
    ):
        values = _mapping(node)
        if values is None:
            return [self._gap(
                root, path, repository, commit, _line(node),
                "invalid-kubernetes-pod-spec", document_index, pointer,
            )]
        result = []
        for category in CONTAINER_CATEGORIES:
            entry = values.get(category)
            category_pointer = _pointer(pointer, category)
            if entry is None:
                if category == "containers":
                    result.append(self._gap(
                        root, path, repository, commit, _line(node),
                        "missing-kubernetes-containers", document_index,
                        category_pointer,
                    ))
                continue
            key, array = entry
            if not isinstance(array, SequenceNode):
                result.append(self._gap(
                    root, path, repository, commit, _line(array),
                    "invalid-kubernetes-container-array", document_index,
                    category_pointer,
                ))
                continue
            if category == "containers" and not array.value:
                result.append(self._gap(
                    root, path, repository, commit, _line(key),
                    "missing-kubernetes-containers", document_index,
                    category_pointer,
                ))
                continue
            for index, container in enumerate(array.value):
                state["containers"] += 1
                if state["containers"] > MAX_CONTAINERS:
                    raise _ResourceLimit()
                container_pointer = _pointer(category_pointer, index)
                container_values = _mapping(container)
                if container_values is None:
                    result.append(self._gap(
                        root, path, repository, commit, _line(container),
                        "invalid-kubernetes-container", document_index,
                        container_pointer,
                    ))
                    continue
                image_entry = container_values.get("image")
                image_pointer = _pointer(container_pointer, "image")
                image_node = image_entry[1] if image_entry is not None else None
                image = _literal(image_node)
                if image is None or not image:
                    result.append(self._gap(
                        root, path, repository, commit,
                        _line(image_node if image_node is not None else container),
                        "missing-kubernetes-image", document_index, image_pointer,
                    ))
                elif any(marker in image for marker in DYNAMIC_MARKERS):
                    result.append(self._gap(
                        root, path, repository, commit, _line(image_node),
                        "dynamic-kubernetes-image", document_index, image_pointer,
                    ))
                elif image == "scratch" or IMAGE_NAME.fullmatch(image) is None:
                    result.append(self._gap(
                        root, path, repository, commit, _line(image_node),
                        "invalid-kubernetes-image", document_index, image_pointer,
                    ))
                else:
                    result.append(self._image(
                        root, path, repository, commit, _line(image_node), image,
                        api_version, workload_kind, category, document_index,
                        image_pointer,
                    ))
        return result

    def _workload(
        self, root, path, repository, commit, node, api_version, kind,
        document_index, pointer, state,
    ):
        current = node
        current_pointer = pointer
        for part in SUPPORTED_WORKLOADS[(api_version, kind)]:
            values = _mapping(current)
            current_pointer = _pointer(current_pointer, part)
            if values is None or part not in values:
                return [self._gap(
                    root, path, repository, commit, _line(current),
                    "invalid-kubernetes-workload", document_index, current_pointer,
                )]
            current = values[part][1]
        return self._pod_spec(
            root, path, repository, commit, current, api_version, kind,
            document_index, current_pointer, state,
        )

    def _resource(
        self, root, path, repository, commit, node, document_index, pointer,
        state, list_depth=0, expected=None,
    ):
        state["resources"] += 1
        if state["resources"] > MAX_RESOURCES or list_depth > MAX_LIST_DEPTH:
            raise _ResourceLimit()
        values = _mapping(node)
        if values is None:
            return [self._gap(
                root, path, repository, commit, _line(node),
                "invalid-kubernetes-list-item" if pointer else "invalid-kubernetes-document",
                document_index, pointer,
            )]
        version_entry = values.get("apiVersion")
        kind_entry = values.get("kind")
        version_node = version_entry[1] if version_entry is not None else None
        kind_node = kind_entry[1] if kind_entry is not None else None
        api_version = _literal(version_node)
        kind = _literal(kind_node)
        if expected is not None:
            expected_version, expected_kind = expected
            if version_entry is None:
                api_version = expected_version
            elif api_version != expected_version:
                return [self._gap(
                    root, path, repository, commit, _line(version_node),
                    "unsupported-kubernetes-version", document_index,
                    _pointer(pointer, "apiVersion"),
                )]
            if kind_entry is None:
                kind = expected_kind
            elif kind != expected_kind:
                return [self._gap(
                    root, path, repository, commit, _line(kind_node),
                    "unsupported-kubernetes-kind", document_index,
                    _pointer(pointer, "kind"),
                )]
        if api_version is None or kind is None:
            return [self._gap(
                root, path, repository, commit,
                _line(version_node or kind_node or node),
                "missing-kubernetes-type", document_index, pointer,
            )]
        if kind in KNOWN_NON_IMAGE_KINDS:
            return []
        if (api_version, kind) in SUPPORTED_WORKLOADS:
            return self._workload(
                root, path, repository, commit, node, api_version, kind,
                document_index, pointer, state,
            )
        typed = TYPED_LISTS.get((api_version, kind))
        generic = (api_version, kind) == ("v1", "List")
        if typed is not None or generic:
            items_entry = values.get("items")
            items_pointer = _pointer(pointer, "items")
            if items_entry is None or not isinstance(items_entry[1], SequenceNode):
                return [self._gap(
                    root, path, repository, commit,
                    _line(items_entry[1] if items_entry is not None else node),
                    "invalid-kubernetes-list", document_index, items_pointer,
                )]
            result = []
            for index, item in enumerate(items_entry[1].value):
                item_pointer = _pointer(items_pointer, index)
                if not isinstance(item, MappingNode):
                    result.append(self._gap(
                        root, path, repository, commit, _line(item),
                        "invalid-kubernetes-list-item", document_index, item_pointer,
                    ))
                    continue
                result.extend(self._resource(
                    root, path, repository, commit, item, document_index,
                    item_pointer, state, list_depth + 1, typed,
                ))
            return result
        supported_versions = {
            version for version, supported_kind in SUPPORTED_WORKLOADS
            if supported_kind == kind
        }
        supported_versions.update(
            version for version, supported_kind in TYPED_LISTS
            if supported_kind == kind
        )
        code = (
            "unsupported-kubernetes-version"
            if supported_versions else "unsupported-kubernetes-kind"
        )
        target = version_node if code == "unsupported-kubernetes-version" else kind_node
        field = "apiVersion" if code == "unsupported-kubernetes-version" else "kind"
        return [self._gap(
            root, path, repository, commit, _line(target), code,
            document_index, _pointer(pointer, field),
        )]

    def _extract(self, root, path, repository, commit, text):
        try:
            _preflight_events(text)
            try:
                documents = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
            except (yaml.YAMLError, RecursionError, MemoryError) as error:
                mark = getattr(error, "problem_mark", None)
                raise _YamlFatal(
                    "malformed-kubernetes-yaml",
                    mark.line + 1 if mark is not None else 1,
                )
            _validate_nodes(documents)
        except _YamlFatal as error:
            return [self._gap(
                root, path, repository, commit, error.line, error.code
            )]

        state = {"resources": 0, "containers": 0}
        result = []
        try:
            for document_index, document in enumerate(documents):
                if document is None or (
                    isinstance(document, ScalarNode)
                    and document.tag == "tag:yaml.org,2002:null"
                ):
                    continue
                result.extend(self._resource(
                    root, path, repository, commit, document, document_index,
                    "", state,
                ))
        except _ResourceLimit:
            return [self._gap(
                root, path, repository, commit, 1,
                "kubernetes-parser-resource-limit",
            )]
        unique = {item["id"]: item for item in result}
        return [unique[item_id] for item_id in sorted(unique)]

    def detect(self, root, repository, commit):
        selection_root = root / Path(self.selection["subpath"])
        candidates, excluded = _candidate_files(root, selection_root)
        observations = []
        for path in candidates:
            relative = path.relative_to(root).as_posix()
            try:
                with path.open("rb") as source:
                    content = source.read(MAX_FILE_BYTES + 1)
                if len(content) > MAX_FILE_BYTES:
                    excluded.append({"path": relative, "reason": "file-too-large"})
                    continue
                text = content.decode("utf-8")
            except (OSError, UnicodeError):
                excluded.append({
                    "path": relative,
                    "reason": "unsupported-or-unreadable-content",
                })
                continue
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            observations.extend(self._extract(
                root, path, repository, commit, text
            ))
        unique = {item["id"]: item for item in observations}
        return (
            [unique[item_id] for item_id in sorted(unique)],
            sorted(excluded, key=lambda item: (item["path"], item["reason"])),
        )
