"""Conservative OpenAPI and AsyncAPI JSON extraction without reference resolution."""

import json
import os
from pathlib import Path
import re
from bisect import bisect_right

from landscape_core.observations import observation
from landscape_core.safety import (
    MAX_FILE_BYTES,
    directory_exclusion_reason,
    exclusion_reason,
)


HTTP_ACTIONS = {
    "delete", "get", "head", "options", "patch", "post", "put", "query", "trace"
}
OPENAPI_VERSION = re.compile(r"^3\.(?:0|1|2)\.[0-9]+$")
ASYNCAPI_VERSION = re.compile(r"^(?:2|3)\.[0-9]+\.[0-9]+$")


class _DuplicateKey(ValueError):
    pass


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _candidate(name):
    lowered = name.lower()
    for extension, serialization in (
        (".json", "json"), (".yaml", "yaml"), (".yml", "yaml")
    ):
        if not lowered.endswith(extension):
            continue
        stem = lowered[: -len(extension)]
        for specification in ("openapi", "asyncapi"):
            if stem == specification or stem.endswith("." + specification):
                return specification, serialization
    return None


def _candidate_files(root):
    found = []
    for current, directory_names, file_names in os.walk(root, topdown=True):
        current_path = Path(current)
        directory_names[:] = [
            name
            for name in sorted(directory_names)
            if not (current_path / name).is_symlink()
            and directory_exclusion_reason((current_path / name).relative_to(root)) is None
        ]
        for file_name in sorted(file_names):
            hint = _candidate(file_name)
            if hint is None:
                continue
            path = current_path / file_name
            if path.is_symlink():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if exclusion_reason(path.relative_to(root), size) is None:
                found.append((path, hint))
    return sorted(found, key=lambda item: item[0].relative_to(root).as_posix())


class _JsonLocations:
    """Map structural JSON object-key pointers to their exact source lines."""

    def __init__(self, text):
        self.text = text
        self.decoder = json.JSONDecoder()
        self.newlines = [index for index, value in enumerate(text) if value == "\n"]
        self.key_lines = {}
        end = self._value(0, ())
        if self._skip(end) != len(text):
            raise ValueError("trailing JSON content")

    def _skip(self, index):
        while index < len(self.text) and self.text[index].isspace():
            index += 1
        return index

    def _line(self, index):
        return bisect_right(self.newlines, index) + 1

    def _value(self, index, pointer):
        index = self._skip(index)
        if self.text[index] == "{":
            return self._object(index, pointer)
        if self.text[index] == "[":
            return self._array(index, pointer)
        _value, end = self.decoder.raw_decode(self.text, index)
        return end

    def _object(self, index, pointer):
        index = self._skip(index + 1)
        if self.text[index] == "}":
            return index + 1
        while True:
            key_start = index
            key, index = self.decoder.raw_decode(self.text, index)
            if not isinstance(key, str):
                raise ValueError("object key is not a string")
            child = pointer + (key,)
            self.key_lines[child] = self._line(key_start)
            index = self._skip(index)
            if self.text[index] != ":":
                raise ValueError("missing object separator")
            index = self._value(index + 1, child)
            index = self._skip(index)
            if self.text[index] == "}":
                return index + 1
            if self.text[index] != ",":
                raise ValueError("missing object delimiter")
            index = self._skip(index + 1)

    def _array(self, index, pointer):
        index = self._skip(index + 1)
        if self.text[index] == "]":
            return index + 1
        item = 0
        while True:
            index = self._value(index, pointer + (item,))
            item += 1
            index = self._skip(index)
            if self.text[index] == "]":
                return index + 1
            if self.text[index] != ",":
                raise ValueError("missing array delimiter")
            index = self._skip(index + 1)

    def line(self, *pointer):
        return self.key_lines.get(tuple(pointer))


def _line_span(lines, *numbers):
    if any(number is None for number in numbers):
        return "1" if len(lines) == 1 else "1-{}".format(len(lines))
    start = min(numbers)
    end = max(numbers)
    return str(start) if start == end else "{}-{}".format(start, end)


class ApiContractDetector:
    name = "api-contract"
    version = 1

    def _item(self, root, path, repository, commit, lines, kind, value):
        return observation(
            kind=kind,
            repository=repository,
            commit=commit,
            detector=self.name,
            detector_version=self.version,
            source_path=path.relative_to(root).as_posix(),
            source_lines=lines,
            value=value,
        )

    def _gap(
        self, root, path, repository, commit, lines, specification, serialization,
        code, detail,
    ):
        return self._item(
            root, path, repository, commit, lines, "api-gap",
            {
                "specification": specification,
                "serialization": serialization,
                "code": code,
                "detail": detail,
            },
        )

    def _document(
        self, root, path, repository, commit, lines, locations, document,
        specification, version,
    ):
        marker_line = locations.line(specification)
        value = {
            "specification": specification,
            "serialization": "json",
            "specificationVersion": version,
        }
        info = document.get("info")
        if isinstance(info, dict) and isinstance(info.get("title"), str) and info["title"]:
            value["title"] = info["title"]
            title_line = locations.line("info", "title")
            source_lines = _line_span(lines, marker_line, title_line)
        else:
            source_lines = _line_span(lines, marker_line)
        return self._item(
            root, path, repository, commit, source_lines, "api-document", value
        )

    def _openapi_operations(
        self, root, path, repository, commit, lines, locations, document
    ):
        paths = document.get("paths")
        if paths is None:
            return []
        if not isinstance(paths, dict):
            return [self._gap(
                root, path, repository, commit,
                _line_span(lines, locations.line("paths")),
                "openapi", "json", "invalid-paths",
                "OpenAPI paths must be an object before operations can be inspected.",
            )]
        observations = []
        supported_actions = HTTP_ACTIONS
        for target in sorted(paths):
            path_line = locations.line("paths", target)
            item = paths[target]
            if not isinstance(target, str) or not target.startswith("/") or not isinstance(item, dict):
                observations.append(self._gap(
                    root, path, repository, commit, _line_span(lines, path_line),
                    "openapi", "json",
                    "invalid-path-item",
                    "OpenAPI path entries require a literal slash-prefixed key and object value.",
                ))
                continue
            for action in sorted(set(item) & supported_actions):
                action_line = locations.line("paths", target, action)
                operation = item[action]
                if not isinstance(operation, dict):
                    observations.append(self._gap(
                        root, path, repository, commit, _line_span(lines, action_line),
                        "openapi", "json",
                        "invalid-operation",
                        "OpenAPI operation entries must be objects.",
                    ))
                    continue
                value = {
                    "specification": "openapi",
                    "operationType": "http",
                    "action": action,
                    "target": target,
                }
                operation_id = operation.get("operationId")
                if operation_id is not None and (
                    not isinstance(operation_id, str) or not operation_id
                ):
                    observations.append(self._gap(
                        root, path, repository, commit,
                        _line_span(
                            lines, locations.line("paths", target, action, "operationId")
                        ),
                        "openapi", "json", "invalid-operation-id",
                        "OpenAPI operationId must be a non-empty literal string.",
                    ))
                    continue
                source_line = action_line
                if operation_id is not None:
                    value["operationId"] = operation_id
                    source_line = locations.line(
                        "paths", target, action, "operationId"
                    )
                observations.append(self._item(
                    root, path, repository, commit,
                    _line_span(lines, path_line, action_line, source_line),
                    "api-operation", value,
                ))
            ignored = {"$ref", "description", "parameters", "servers", "summary"}
            for unsupported in sorted(
                key for key in item
                if key not in supported_actions and key not in ignored
                and not (isinstance(key, str) and key.startswith("x-"))
            ):
                observations.append(self._gap(
                    root, path, repository, commit,
                    _line_span(lines, locations.line("paths", target, unsupported)),
                    "openapi", "json", "unsupported-operation-key",
                    "The OpenAPI path item contains an unsupported operation-shaped key.",
                ))
        return observations

    def _asyncapi2_operations(
        self, root, path, repository, commit, lines, locations, document
    ):
        channels = document.get("channels")
        if channels is None:
            return []
        if not isinstance(channels, dict):
            return [self._gap(
                root, path, repository, commit,
                _line_span(lines, locations.line("channels")),
                "asyncapi", "json", "invalid-channels",
                "AsyncAPI 2 channels must be an object before operations can be inspected.",
            )]
        observations = []
        for channel in sorted(channels):
            channel_line = locations.line("channels", channel)
            item = channels[channel]
            if not isinstance(channel, str) or not channel or not isinstance(item, dict):
                observations.append(self._gap(
                    root, path, repository, commit, _line_span(lines, channel_line),
                    "asyncapi", "json",
                    "invalid-channel",
                    "AsyncAPI 2 channel entries require a non-empty key and object value.",
                ))
                continue
            for action in ("publish", "subscribe"):
                if action not in item:
                    continue
                action_line = locations.line("channels", channel, action)
                operation = item[action]
                if not isinstance(operation, dict):
                    observations.append(self._gap(
                        root, path, repository, commit, _line_span(lines, action_line),
                        "asyncapi", "json",
                        "invalid-operation",
                        "AsyncAPI 2 operation entries must be objects.",
                    ))
                    continue
                value = {
                    "specification": "asyncapi",
                    "operationType": "channel",
                    "action": action,
                    "target": channel,
                }
                operation_id = operation.get("operationId")
                if operation_id is not None and (
                    not isinstance(operation_id, str) or not operation_id
                ):
                    observations.append(self._gap(
                        root, path, repository, commit,
                        _line_span(
                            lines,
                            locations.line("channels", channel, action, "operationId"),
                        ),
                        "asyncapi", "json", "invalid-operation-id",
                        "AsyncAPI 2 operationId must be a non-empty literal string.",
                    ))
                    continue
                source_line = action_line
                if operation_id is not None:
                    value["operationId"] = operation_id
                    source_line = locations.line(
                        "channels", channel, action, "operationId"
                    )
                observations.append(self._item(
                    root, path, repository, commit,
                    _line_span(lines, channel_line, action_line, source_line),
                    "api-operation", value,
                ))
            ignored = {
                "$ref", "bindings", "description", "parameters", "servers",
                "publish", "subscribe",
            }
            for unsupported in sorted(
                key for key in item
                if key not in ignored
                and not (isinstance(key, str) and key.startswith("x-"))
            ):
                observations.append(self._gap(
                    root, path, repository, commit,
                    _line_span(lines, locations.line("channels", channel, unsupported)),
                    "asyncapi", "json", "unsupported-operation-key",
                    "The AsyncAPI 2 channel contains an unsupported operation-shaped key.",
                ))
        return observations

    def _asyncapi3_operations(
        self, root, path, repository, commit, lines, locations, document
    ):
        operations = document.get("operations")
        if operations is None:
            return []
        if not isinstance(operations, dict):
            return [self._gap(
                root, path, repository, commit,
                _line_span(lines, locations.line("operations")),
                "asyncapi", "json", "invalid-operations",
                "AsyncAPI 3 operations must be an object.",
            )]
        observations = []
        for operation_key in sorted(operations):
            operation_line = locations.line("operations", operation_key)
            item = operations[operation_key]
            action = item.get("action") if isinstance(item, dict) else None
            channel = item.get("channel") if isinstance(item, dict) else None
            reference = channel.get("$ref") if isinstance(channel, dict) else None
            if (
                not isinstance(operation_key, str) or not operation_key
                or not isinstance(item, dict) or action not in {"send", "receive"}
                or not isinstance(reference, str) or not reference
            ):
                observations.append(self._gap(
                    root, path, repository, commit, _line_span(lines, operation_line),
                    "asyncapi", "json",
                    "invalid-operation",
                    "AsyncAPI 3 operations require a key, send or receive action, and literal channel $ref.",
                ))
                continue
            action_line = locations.line("operations", operation_key, "action")
            reference_line = locations.line(
                "operations", operation_key, "channel", "$ref"
            )
            observations.append(self._item(
                root, path, repository, commit,
                _line_span(lines, operation_line, action_line, reference_line),
                "api-operation",
                {
                    "specification": "asyncapi",
                    "operationType": "channel",
                    "operationKey": operation_key,
                    "action": action,
                    "target": reference,
                },
            ))
        return observations

    def _extract_json(self, root, path, repository, commit, hint):
        try:
            with path.open("rb") as source:
                content = source.read(MAX_FILE_BYTES + 1)
            if len(content) > MAX_FILE_BYTES:
                return [], [{
                    "path": path.relative_to(root).as_posix(),
                    "reason": "file-too-large",
                }]
            text = content.decode("utf-8")
        except (OSError, UnicodeError):
            return [], [{
                "path": path.relative_to(root).as_posix(),
                "reason": "unsupported-or-unreadable-content",
            }]
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.splitlines() or [""]
        try:
            document = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
        except _DuplicateKey:
            return [self._gap(
                root, path, repository, commit, "1", hint, "json", "duplicate-json-key",
                "JSON API documents with duplicate object keys are not interpreted.",
            )], []
        except json.JSONDecodeError as error:
            return [self._gap(
                root, path, repository, commit, str(error.lineno), hint, "json",
                "malformed-json",
                "The API document is not valid JSON.",
            )], []
        except (RecursionError, MemoryError):
            return [self._gap(
                root, path, repository, commit, "1", hint, "json",
                "parser-resource-limit",
                "The JSON API document exceeds safe parser resource limits.",
            )], []
        if not isinstance(document, dict):
            return [self._gap(
                root, path, repository, commit, "1", hint, "json", "invalid-root",
                "A JSON API document must have an object root.",
            )], []
        try:
            locations = _JsonLocations(text)
        except (IndexError, RecursionError, ValueError):
            return [self._gap(
                root, path, repository, commit, "1", hint, "json",
                "unsupported-json-layout",
                "The JSON layout could not be mapped to stable source lines.",
            )], []
        markers = [name for name in ("openapi", "asyncapi") if name in document]
        if len(markers) != 1:
            code = "conflicting-specification-markers" if markers else "missing-specification-marker"
            detail = (
                "A JSON API document must contain exactly one root openapi or asyncapi marker."
            )
            specification = "unknown" if len(markers) > 1 else hint
            marker_lines = [locations.line(marker) for marker in markers]
            source_lines = _line_span(lines, *marker_lines) if marker_lines else "1"
            return [self._gap(
                root, path, repository, commit, source_lines, specification, "json",
                code, detail,
            )], []
        specification = markers[0]
        version = document[specification]
        marker_line = _line_span(lines, locations.line(specification))
        if not isinstance(version, str) or not version:
            return [self._gap(
                root, path, repository, commit, marker_line, specification, "json",
                "invalid-specification-version",
                "The root specification version must be a non-empty literal string.",
            )], []

        observations = [self._document(
            root, path, repository, commit, lines, locations, document,
            specification, version,
        )]
        supported = (
            OPENAPI_VERSION.fullmatch(version) if specification == "openapi"
            else ASYNCAPI_VERSION.fullmatch(version)
        )
        if supported is None:
            observations.append(self._gap(
                root, path, repository, commit, marker_line, specification, "json",
                "unsupported-version",
                "The specification version is recognized but not supported by detector version 1.",
            ))
            return observations, []
        if specification == "openapi":
            observations.extend(self._openapi_operations(
                root, path, repository, commit, lines, locations, document
            ))
        elif version.startswith("2."):
            observations.extend(self._asyncapi2_operations(
                root, path, repository, commit, lines, locations, document
            ))
        else:
            observations.extend(self._asyncapi3_operations(
                root, path, repository, commit, lines, locations, document
            ))
        return observations, []

    def detect(self, root, repository, commit):
        observations = []
        excluded = []
        for path, (hint, serialization) in _candidate_files(root):
            if serialization == "yaml":
                observations.append(self._gap(
                    root, path, repository, commit, "1", hint, "yaml",
                    "unsupported-yaml",
                    "YAML API documents are identified by filename but are not parsed in detector version 1.",
                ))
                continue
            found, skipped = self._extract_json(root, path, repository, commit, hint)
            observations.extend(found)
            excluded.extend(skipped)
        return observations, excluded
