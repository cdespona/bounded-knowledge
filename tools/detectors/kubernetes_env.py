"""Allowlisted infrastructure environment variables from one reviewed Kubernetes YAML selection."""

from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from landscape_core.env_vars import classify_env, describe_env
from landscape_core.observations import observation
from landscape_core.safety import MAX_FILE_BYTES
from .kubernetes_images import (
    CONTAINER_CATEGORIES, SUPPORTED_WORKLOADS, TYPED_LISTS, _YamlFatal, _candidate_files, _line, _literal,
    _mapping, _preflight_events, _validate_nodes,
)

VALUE_FROM = {"secretKeyRef", "configMapKeyRef", "fieldRef", "resourceFieldRef"}
MAX_DEPTH = 8


class KubernetesEnvDetector:
    name = "kubernetes-env"
    version = 1

    def __init__(self, source_selection):
        self.selection = dict(source_selection)

    def _item(self, root, path, repository, commit, first, last, value):
        return observation(
            kind="env-variable", repository=repository, commit=commit, detector=self.name,
            detector_version=self.version, source_path=path.relative_to(root).as_posix(),
            source_lines=str(first) if first == last else "{}-{}".format(first, last), value=value)

    def _entry(self, root, path, repository, commit, workload, container, entry):
        fields = _mapping(entry)
        name = _literal(fields["name"][1]) if fields and "name" in fields else None
        if not name:
            return []
        first = _line(fields["name"][0])
        base = {"name": name, "container": container, "workload": workload,
                "sourceSelectionId": self.selection["id"]}
        if "valueFrom" in fields:
            resource = classify_env(name, None)
            source = _mapping(fields["valueFrom"][1])
            if resource is None or source is None:
                return []
            kind = next((key for key in source if key in VALUE_FROM), "other")
            line = _line(fields["valueFrom"][0])
            return [self._item(root, path, repository, commit, first, max(first, line), dict(
                base, disposition="reference", value=None, resource=resource, reference=kind, valueLine=line))]
        if "value" not in fields or not isinstance(fields["value"][1], ScalarNode):
            return []
        node = fields["value"][1]
        resource = classify_env(name, node.value)
        if resource is None:
            return []
        disposition, stored = describe_env(name, node.value)
        value_line = _line(fields["value"][0])
        last = node.end_mark.line if node.end_mark.column == 0 else node.end_mark.line + 1
        return [self._item(root, path, repository, commit, first, max(first, last), dict(
            base, disposition=disposition, value=stored, resource=resource, reference=None, valueLine=value_line))]

    def _pod_spec(self, root, path, repository, commit, node, workload):
        values = _mapping(node)
        result = []
        for category in CONTAINER_CATEGORIES:
            entry = (values or {}).get(category)
            if entry is None or not isinstance(entry[1], SequenceNode):
                continue
            for container in entry[1].value:
                fields = _mapping(container)
                if fields is None or "env" not in fields or not isinstance(fields["env"][1], SequenceNode):
                    continue
                container_name = _literal(fields["name"][1]) if "name" in fields else None
                for item in fields["env"][1].value:
                    result.extend(self._entry(root, path, repository, commit, workload, container_name or "", item))
        return result

    def _resource(self, root, path, repository, commit, node, depth=0, expected=None):
        values = _mapping(node)
        if values is None or depth > MAX_DEPTH:
            return []
        api_version = _literal(values["apiVersion"][1]) if "apiVersion" in values else None
        kind = _literal(values["kind"][1]) if "kind" in values else None
        if expected is not None:
            api_version, kind = api_version or expected[0], kind or expected[1]
        if (api_version, kind) in SUPPORTED_WORKLOADS:
            metadata = _mapping(values["metadata"][1]) if "metadata" in values else None
            name = _literal(metadata["name"][1]) if metadata and "name" in metadata else None
            current = node
            for part in SUPPORTED_WORKLOADS[(api_version, kind)]:
                fields = _mapping(current)
                if fields is None or part not in fields:
                    return []
                current = fields[part][1]
            return self._pod_spec(root, path, repository, commit, current, "{}/{}".format(kind, name or ""))
        typed = TYPED_LISTS.get((api_version, kind))
        if (typed is not None or (api_version, kind) == ("v1", "List")) and "items" in values:
            items = values["items"][1]
            result = []
            if isinstance(items, SequenceNode):
                for item in items.value:
                    result.extend(self._resource(root, path, repository, commit, item, depth + 1, typed))
            return result
        return []

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
                    continue
                text = content.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                _preflight_events(text)
                documents = list(yaml.compose_all(text, Loader=yaml.SafeLoader))
                _validate_nodes(documents)
            except (OSError, UnicodeError, _YamlFatal, yaml.YAMLError, RecursionError, MemoryError):
                continue
            for document in documents:
                if document is not None:
                    observations.extend(self._resource(root, path, repository, commit, document))
        unique = {item["id"]: item for item in observations}
        return [unique[item_id] for item_id in sorted(unique)], []
