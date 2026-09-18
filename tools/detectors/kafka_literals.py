"""Conservative literal Kafka source and properties inventory."""

import os
from pathlib import Path
import re
import unicodedata

from landscape_core.observations import observation
from landscape_core.safety import (
    MAX_FILE_BYTES,
    directory_exclusion_reason,
    exclusion_reason,
)


SOURCE_MARKERS = (
    "NewTopic",
    "KafkaTemplate",
    "KafkaListener",
    "SchemaRegistryClient",
)
DEFAULT_TOPIC_KEY = "spring.kafka.template.default-topic"
PROFILE_PROPERTIES = re.compile(r"^application(?:-[A-Za-z0-9._-]+)?\.properties$")
IMPORTS = {
    "NewTopic": "org.apache.kafka.clients.admin.NewTopic",
    "KafkaTemplate": "org.springframework.kafka.core.KafkaTemplate",
    "KafkaListener": "org.springframework.kafka.annotation.KafkaListener",
    "SchemaRegistryClient": (
        "io.confluent.kafka.schemaregistry.client.SchemaRegistryClient"
    ),
}

DETAILS = {
    "dynamic-topic": "The Kafka topic expression is not a supported literal.",
    "interpolated-topic": "Interpolated Kafka topic strings are not interpreted.",
    "property-derived-topic": "Property-derived Kafka topics are not resolved.",
    "spread-topic": "Spread Kafka listener topics are not interpreted.",
    "unsupported-multi-topic": "Kafka listener registrations with multiple topics are not interpreted.",
    "ambiguous-kafka-binding": "The Kafka receiver has multiple supported same-file bindings.",
    "unsupported-kafka-import": "Wildcard, static, and aliased Kafka imports are not supported.",
    "unsupported-producer-form": "The Kafka producer construct is outside the version 1 allowlist.",
    "unsupported-consumer-form": "The Kafka consumer construct is outside the version 1 allowlist.",
    "unsupported-topic-declaration": "The Kafka topic declaration is outside the version 1 allowlist.",
    "dynamic-schema-subject": "The Schema Registry subject expression is not a supported literal.",
    "interpolated-schema-subject": "Interpolated Schema Registry subjects are not interpreted.",
    "unsupported-schema-reference": "The schema reference construct is outside the version 1 allowlist.",
    "malformed-kafka-construct": "The Kafka construct is incomplete or cannot be tokenized safely.",
    "unsupported-source-literal": "Text blocks and raw strings are not supported Kafka literals.",
    "unsupported-unicode-escape": "Java Unicode escapes are not interpreted in Kafka candidates.",
    "duplicate-config-key": "The supported Kafka properties key occurs more than once.",
    "unsupported-config-syntax": "The Kafka properties entry uses unsupported syntax.",
    "unsupported-property-value": "The Kafka properties value is not a supported literal.",
    "unsupported-literal-value": "Kafka literal values containing control characters are not supported.",
}


class _Token:
    def __init__(self, kind, value, line, *, escaped=False, raw=False):
        self.kind = kind
        self.value = value
        self.line = line
        self.escaped = escaped
        self.raw = raw


def _tokens(text, language):
    tokens = []
    index = 0
    line = 1
    error_line = None
    length = len(text)
    while index < length:
        value = text[index]
        if value == "\n":
            line += 1
            index += 1
            continue
        if value.isspace():
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index + 2)
            index = length if end < 0 else end
            continue
        if text.startswith("/*", index):
            start_line = line
            end = text.find("*/", index + 2)
            if end < 0:
                error_line = start_line
                break
            line += text[index:end + 2].count("\n")
            index = end + 2
            continue
        if text.startswith('"""', index):
            start_line = line
            end = text.find('"""', index + 3)
            if end < 0:
                error_line = start_line
                break
            content = text[index + 3:end]
            tokens.append(_Token("string", content, start_line, raw=True))
            line += text[index:end + 3].count("\n")
            index = end + 3
            continue
        if value == '"':
            start_line = line
            index += 1
            content = []
            escaped = False
            closed = False
            while index < length:
                current = text[index]
                if current == "\n":
                    break
                if current == "\\":
                    escaped = True
                    content.append(current)
                    index += 1
                    if index < length and text[index] != "\n":
                        content.append(text[index])
                        index += 1
                    continue
                if current == '"':
                    index += 1
                    closed = True
                    break
                content.append(current)
                index += 1
            if not closed:
                error_line = start_line
                break
            tokens.append(_Token("string", "".join(content), start_line, escaped=escaped))
            continue
        if value == "'":
            start_line = line
            index += 1
            escaped = False
            while index < length and text[index] != "\n":
                if text[index] == "\\" and not escaped:
                    escaped = True
                    index += 2
                    escaped = False
                    continue
                if text[index] == "'":
                    index += 1
                    break
                index += 1
            else:
                error_line = start_line
                break
            continue
        if value.isalpha() or value in "_$":
            end = index + 1
            while end < length and (text[end].isalnum() or text[end] in "_$"):
                end += 1
            tokens.append(_Token("identifier", text[index:end], line))
            index = end
            continue
        tokens.append(_Token("symbol", value, line))
        index += 1
    return tokens, error_line


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
            path = current_path / file_name
            if path.is_symlink():
                continue
            language = None
            if path.suffix == ".java":
                language = "java"
            elif path.suffix == ".kt":
                language = "kotlin"
            elif PROFILE_PROPERTIES.fullmatch(path.name):
                language = "properties"
            if language is None:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if exclusion_reason(path.relative_to(root), size) is None:
                found.append((path, language))
    return sorted(found, key=lambda item: item[0].relative_to(root).as_posix())


def _by_line(tokens):
    result = {}
    for token in tokens:
        result.setdefault(token.line, []).append(token)
    return result


def _values(tokens):
    return [token.value for token in tokens]


def _imports(lines):
    exact = set()
    unsupported = []
    for line, tokens in sorted(lines.items()):
        values = _values(tokens)
        for start, value in enumerate(values):
            if value != "import":
                continue
            try:
                end = values.index(";", start + 1)
            except ValueError:
                end = len(values)
            body = values[start + 1:end]
            rendered = "".join(body)
            for marker, expected in IMPORTS.items():
                if rendered == expected:
                    exact.add(marker)
                elif (
                    marker in rendered
                    or (
                        rendered.endswith(".*")
                        and expected.rsplit(".", 1)[0] == rendered[:-2]
                    )
                ) and ("*" in body or "static" in body or "as" in body):
                    unsupported.append((line, marker))
    return exact, unsupported


def _matching_close(tokens, open_index, opening="(", closing=")"):
    depth = 0
    for index in range(open_index, len(tokens)):
        if tokens[index].value == opening:
            depth += 1
        elif tokens[index].value == closing:
            depth -= 1
            if depth == 0:
                return index
    return None


def _split_arguments(tokens):
    arguments = []
    current = []
    depths = {"(": 0, "[": 0, "{": 0}
    pairs = {")": "(", "]": "[", "}": "{"}
    for token in tokens:
        if token.value in depths:
            depths[token.value] += 1
        elif token.value in pairs:
            depths[pairs[token.value]] -= 1
        if token.value == "," and all(depth == 0 for depth in depths.values()):
            arguments.append(current)
            current = []
        else:
            current.append(token)
    arguments.append(current)
    return arguments


def _literal_result(tokens, language, topic=True):
    if len(tokens) == 1 and tokens[0].kind == "string":
        token = tokens[0]
        if token.raw:
            return None, "unsupported-source-literal"
        if token.escaped:
            return None, "dynamic-topic" if topic else "dynamic-schema-subject"
        if any(unicodedata.category(character) == "Cc" for character in token.value):
            return None, "unsupported-literal-value"
        if (
            (language == "kotlin" and "$" in token.value)
            or "${" in token.value
            or "#{" in token.value
        ):
            if topic and "${" in token.value:
                return None, "property-derived-topic"
            return None, "interpolated-topic" if topic else "interpolated-schema-subject"
        if token.value:
            return token.value, None
    if len(tokens) == 1 and tokens[0].kind == "identifier":
        return None, "property-derived-topic" if topic else "dynamic-schema-subject"
    return None, "dynamic-topic" if topic else "dynamic-schema-subject"


def _bindings(lines, type_name, require_generic=True):
    bindings = {}
    for _line, tokens in lines.items():
        values = _values(tokens)
        for index, value in enumerate(values):
            if value != type_name:
                continue
            if index + 1 < len(values) and values[index + 1] == "<":
                close = _matching_close(tokens, index + 1, "<", ">")
                if close is None:
                    continue
            elif require_generic:
                continue
            else:
                close = index
            identifier = None
            if close + 1 < len(tokens) and tokens[close + 1].kind == "identifier":
                following = (
                    tokens[close + 2].value if close + 2 < len(tokens) else None
                )
                if following in {";", "=", ",", ")"}:
                    identifier = tokens[close + 1].value
            elif index >= 2 and values[index - 1] == ":" and tokens[index - 2].kind == "identifier":
                identifier = tokens[index - 2].value
            if identifier is not None:
                bindings[identifier] = bindings.get(identifier, 0) + 1
    return bindings


class KafkaLiteralDetector:
    name = "kafka-literals"
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

    def _gap(self, root, path, repository, commit, line, context, form, code):
        return self._item(
            root, path, repository, commit, line, "kafka-gap",
            {
                "context": context,
                "form": form,
                "code": code,
                "detail": DETAILS[code],
            },
        )

    def _topic(self, root, path, repository, commit, line, role, form, topic):
        return self._item(
            root, path, repository, commit, line, "kafka-topic-reference",
            {"role": role, "form": form, "topic": topic},
        )

    def _schema(self, root, path, repository, commit, line, subject):
        return self._item(
            root, path, repository, commit, line, "kafka-schema-reference",
            {
                "role": "schema-lookup",
                "form": "schema-registry-subject-lookup",
                "subject": subject,
            },
        )

    def _source(self, root, path, repository, commit, text, language):
        if not any(marker in text for marker in SOURCE_MARKERS):
            return []
        tokens, _error_line = _tokens(text, language)
        lines = _by_line(tokens)
        imports, unsupported_imports = _imports(lines)
        qualified = bool(imports or unsupported_imports)
        if language == "java" and "\\u" in text and qualified:
            line = text[:text.index("\\u")].count("\n") + 1
            return [self._gap(
                root, path, repository, commit, line, "configuration", "kafka-source",
                "unsupported-unicode-escape",
            )]
        result = []
        contexts = {
            "NewTopic": ("topic-declaration", "new-topic"),
            "KafkaTemplate": ("producer-send", "kafka-template-send"),
            "KafkaListener": ("consumer-registration", "kafka-listener"),
            "SchemaRegistryClient": (
                "schema-reference", "schema-registry-subject-lookup"
            ),
        }
        for line, marker in unsupported_imports:
            context, form = contexts[marker]
            result.append(self._gap(
                root, path, repository, commit, line, context, form,
                "unsupported-kafka-import",
            ))

        if "NewTopic" in imports:
            for line, line_tokens in sorted(lines.items()):
                values = _values(line_tokens)
                for index, value in enumerate(values):
                    if value != "NewTopic" or index + 1 >= len(values) or values[index + 1] != "(":
                        continue
                    if language == "java" and (index == 0 or values[index - 1] != "new"):
                        continue
                    close = _matching_close(line_tokens, index + 1)
                    if close is None:
                        result.append(self._gap(
                            root, path, repository, commit, line, "topic-declaration",
                            "new-topic", "malformed-kafka-construct",
                        ))
                        continue
                    arguments = _split_arguments(line_tokens[index + 2:close])
                    if len(arguments) < 2:
                        result.append(self._gap(
                            root, path, repository, commit, line, "topic-declaration",
                            "new-topic", "unsupported-topic-declaration",
                        ))
                        continue
                    literal, code = _literal_result(arguments[0], language)
                    if code:
                        result.append(self._gap(
                            root, path, repository, commit, line, "topic-declaration",
                            "new-topic", code,
                        ))
                    else:
                        result.append(self._topic(
                            root, path, repository, commit, line, "declaration",
                            "new-topic", literal,
                        ))

        if "KafkaTemplate" in imports:
            bindings = _bindings(lines, "KafkaTemplate")
            for line, line_tokens in sorted(lines.items()):
                values = _values(line_tokens)
                for index in range(len(values) - 3):
                    if not (
                        line_tokens[index].kind == "identifier"
                        and values[index + 1:index + 4] == [".", "send", "("]
                    ):
                        continue
                    receiver = values[index]
                    if receiver not in bindings:
                        continue
                    if bindings[receiver] != 1:
                        result.append(self._gap(
                            root, path, repository, commit, line, "producer-send",
                            "kafka-template-send", "ambiguous-kafka-binding",
                        ))
                        continue
                    close = _matching_close(line_tokens, index + 3)
                    if close is None:
                        code = "malformed-kafka-construct"
                    else:
                        arguments = _split_arguments(line_tokens[index + 4:close])
                        if len(arguments) < 2:
                            code = "unsupported-producer-form"
                        else:
                            literal, code = _literal_result(arguments[0], language)
                    if code:
                        result.append(self._gap(
                            root, path, repository, commit, line, "producer-send",
                            "kafka-template-send", code,
                        ))
                    else:
                        result.append(self._topic(
                            root, path, repository, commit, line, "producer-send",
                            "kafka-template-send", literal,
                        ))
                for index in range(len(values) - 3):
                    if not (
                        line_tokens[index].kind == "identifier"
                        and values[index + 1:index + 4] == [".", "sendDefault", "("]
                        and values[index] in bindings
                    ):
                        continue
                    result.append(self._gap(
                        root, path, repository, commit, line, "producer-send",
                        "kafka-template-send", "unsupported-producer-form",
                    ))

        if "KafkaListener" in imports:
            for line, line_tokens in sorted(lines.items()):
                values = _values(line_tokens)
                for index in range(len(values) - 2):
                    if values[index:index + 3] != ["@", "KafkaListener", "("]:
                        continue
                    close = _matching_close(line_tokens, index + 2)
                    if close is None:
                        code = "malformed-kafka-construct"
                        literal = None
                    else:
                        body = line_tokens[index + 3:close]
                        body_values = _values(body)
                        literal = None
                        code = None
                        if body_values[:2] != ["topics", "="]:
                            code = "unsupported-consumer-form"
                        else:
                            value_tokens = body[2:]
                            if any(token.value == "*" for token in value_tokens):
                                code = "spread-topic"
                            elif value_tokens and value_tokens[0].value == "[" and value_tokens[-1].value == "]":
                                inner = value_tokens[1:-1]
                                if any(token.value == "," for token in inner):
                                    code = "unsupported-multi-topic"
                                else:
                                    literal, code = _literal_result(inner, language)
                            else:
                                if any(token.value == "," for token in value_tokens):
                                    code = "unsupported-multi-topic"
                                else:
                                    literal, code = _literal_result(value_tokens, language)
                    if code:
                        result.append(self._gap(
                            root, path, repository, commit, line,
                            "consumer-registration", "kafka-listener", code,
                        ))
                    else:
                        result.append(self._topic(
                            root, path, repository, commit, line,
                            "consumer-registration", "kafka-listener", literal,
                        ))

        if "SchemaRegistryClient" in imports:
            bindings = _bindings(lines, "SchemaRegistryClient", require_generic=False)
            for line, line_tokens in sorted(lines.items()):
                values = _values(line_tokens)
                for index in range(len(values) - 3):
                    if not (
                        line_tokens[index].kind == "identifier"
                        and values[index + 1:index + 4]
                        == [".", "getLatestSchemaMetadata", "("]
                    ):
                        continue
                    receiver = values[index]
                    if receiver not in bindings:
                        continue
                    if bindings[receiver] != 1:
                        code = "ambiguous-kafka-binding"
                        literal = None
                    else:
                        close = _matching_close(line_tokens, index + 3)
                        if close is None:
                            code = "malformed-kafka-construct"
                            literal = None
                        else:
                            arguments = _split_arguments(line_tokens[index + 4:close])
                            if len(arguments) != 1:
                                code = "unsupported-schema-reference"
                                literal = None
                            else:
                                literal, code = _literal_result(
                                    arguments[0], language, topic=False
                                )
                    if code:
                        result.append(self._gap(
                            root, path, repository, commit, line, "schema-reference",
                            "schema-registry-subject-lookup", code,
                        ))
                    else:
                        result.append(self._schema(
                            root, path, repository, commit, line, literal,
                        ))
                for index in range(len(values) - 3):
                    if not (
                        line_tokens[index].kind == "identifier"
                        and values[index + 1] == "."
                        and values[index + 2].startswith("get")
                        and values[index + 2] != "getLatestSchemaMetadata"
                        and values[index + 3] == "("
                        and values[index] in bindings
                    ):
                        continue
                    result.append(self._gap(
                        root, path, repository, commit, line, "schema-reference",
                        "schema-registry-subject-lookup", "unsupported-schema-reference",
                    ))
        return result

    def _properties(self, root, path, repository, commit, text):
        if DEFAULT_TOPIC_KEY not in text:
            return []
        entries = []
        for line_number, line in enumerate(text.split("\n"), 1):
            if line.startswith(("#", "!")):
                continue
            leading = line[:len(line) - len(line.lstrip(" \t"))]
            candidate = line[len(leading):]
            if not candidate.startswith(DEFAULT_TOPIC_KEY):
                continue
            remainder = candidate[len(DEFAULT_TOPIC_KEY):]
            if remainder and remainder[0] not in "=: \t":
                continue
            entries.append((line_number, remainder, bool(leading)))
        if len(entries) > 1:
            return [self._gap(
                root, path, repository, commit, entries[0][0], "configuration",
                "default-topic-property", "duplicate-config-key",
            )]
        if not entries:
            return []
        line, remainder, has_leading_whitespace = entries[0]
        if has_leading_whitespace or not remainder.startswith("="):
            return [self._gap(
                root, path, repository, commit, line, "configuration",
                "default-topic-property", "unsupported-config-syntax",
            )]
        value = remainder[1:]
        result = []
        if (
            not value or value != value.strip() or "\\" in value or "$" in value
            or "#{" in value
        ):
            result.append(self._gap(
                root, path, repository, commit, line, "configuration",
                "default-topic-property", "unsupported-property-value",
            ))
        else:
            result.append(self._topic(
                root, path, repository, commit, line, "producer-default",
                "default-topic-property", value,
            ))
        return result

    def detect(self, root, repository, commit):
        observations = []
        excluded = []
        for path, language in _candidate_files(root):
            relative = path.relative_to(root).as_posix()
            try:
                with path.open("rb") as source:
                    content = source.read(MAX_FILE_BYTES + 1)
                if len(content) > MAX_FILE_BYTES:
                    continue
                text = content.decode("utf-8")
            except (OSError, UnicodeError):
                excluded.append({
                    "path": relative,
                    "reason": "unsupported-or-unreadable-content",
                })
                continue
            text = text.replace("\r\n", "\n").replace("\r", "\n")
            if language == "properties":
                observations.extend(self._properties(
                    root, path, repository, commit, text
                ))
            else:
                observations.extend(self._source(
                    root, path, repository, commit, text, language
                ))
        unique = {item["id"]: item for item in observations}
        return [unique[item_id] for item_id in sorted(unique)], excluded
