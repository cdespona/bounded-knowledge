"""Messaging and persistence I/O points read from Java and Kotlin sources.

The detector reports where code produces to or consumes from a queue, topic, or channel and where it
reads or writes persistent storage, together with the literal, configuration key, or channel name that
names the target. It never decides what the target means.
"""

import os
from pathlib import Path
import re

from landscape_core.config_files import mask_account_ids
from landscape_core.jvm_source import (
    annotations, declarations, group_end, line_of, line_starts, scan, split_arguments, statement_end,
)
from landscape_core.observations import observation
from landscape_core.safety import MAX_FILE_BYTES, directory_exclusion_reason, exclusion_reason


_VALUE = re.compile(
    r'@Value\(\s*"\\?\$\{([^}:"]+)(?::[^}"]*)?\}"\s*\)\s*(?:(?:private|final|val|var)\s+)*'
    r'(?:[\w.<>\[\]?]+\s+)?(\w+)\s*[,)=;:]')
_CONFIG_PROPERTY = re.compile(
    r'@ConfigProperty\(\s*name\s*=\s*"([^"]+)"[^)]*\)\s*(?:(?:private|protected|final|val|var|lateinit)\s+)*'
    r'(?:[\w.<>\[\]?]+\s+)?(\w+)\s*[,)=;:]')
_ALIAS = re.compile(r"\bthis\.(\w+)\s*=\s*(\w+)\s*;")
_CONSTANT = re.compile(
    r'(?:static\s+final|final\s+static|const\s+val)\s+(?:String\s+)?(\w+)\s*(?::\s*String\s*)?=\s*"((?:[^"\\\n]|\\.)*)"')
_RECORD = re.compile(r"\brecord\s+(\w+)\s*\(")
_DATA_CLASS = re.compile(r"\bdata\s+class\s+(\w+)\s*\(")
_METHOD = re.compile(
    r"\b(\w+)\s*\(([^()]*)\)\s*(?:throws\s+[\w.,\s]+)?(?::\s*[\w.<>?,\s]+)?\{")
_NOT_METHODS = {"if", "for", "while", "switch", "catch", "synchronized", "return", "when", "try", "else"}
_CONFIG_CALL = (r'(?:System\.getenv|getenv|(?:[\w.]+\.)?getProperty|(?:[\w.()]+\.)?getOptionalValue|'
                r'(?:[\w.()]+\.)?getValue)')
_CONFIG_READ = re.compile(r'^(?:Optional\.ofNullable\(\s*|Objects\.requireNonNullElse\(\s*)?' + _CONFIG_CALL + r'\(\s*"([^"]+)"')
_ASSIGNMENT = re.compile(r'\b(\w+)\s*=\s*(?:this\.)?((?:Optional\.ofNullable\(\s*|Objects\.requireNonNullElse\(\s*)?'
                         + _CONFIG_CALL + r'\(\s*"[^"]+")')
_CONFIGURATION_PROPERTIES = re.compile(r'^(?:prefix\s*=\s*|value\s*=\s*)?"([^"]+)"$')
_FIELD = re.compile(r'(?:private|protected|public)?\s*(?:final\s+)?[\w.<>\[\], ?]+\s+(\w+)\s*(?:=[^;]*)?;')
_STRING = re.compile(r'^"((?:[^"\\\n]|\\.)*)"$')
_PLACEHOLDER = re.compile(r"^\$\{([^}:]+)(?::.*)?\}$")
_ACCESSOR = re.compile(r"^(?:[\w.()]+\.)?(?:get)?(\w+)\(\)$|^[A-Za-z_][\w.]*\.(\w+)$")
_IDENTIFIER = re.compile(r"^(?:this\.)?(\w+)$")
_TABLE = r'(?:\$\w+\$|\$\{[^}]+\}|%s|[A-Za-z_][\w$#.\"`]*)'
_WRITES = (
    re.compile(r"\bINSERT\s+(?:ALL\s+|OR\s+\w+\s+)?INTO\s+(" + _TABLE + ")", re.IGNORECASE),
    re.compile(r"\bMERGE\s+INTO\s+(" + _TABLE + ")", re.IGNORECASE),
    re.compile(r"\bREPLACE\s+INTO\s+(" + _TABLE + ")", re.IGNORECASE),
    re.compile(r"^\s*UPDATE\s+(" + _TABLE + r")\s", re.IGNORECASE),
    re.compile(r"\bDELETE\s+FROM\s+(" + _TABLE + ")", re.IGNORECASE),
    re.compile(r"\bTRUNCATE\s+TABLE\s+(" + _TABLE + ")", re.IGNORECASE),
)
_READS = (
    re.compile(r"\b(?:FROM|JOIN)\s+(" + _TABLE + ")", re.IGNORECASE),
    re.compile(r"\bUSING\s+(" + _TABLE + ")", re.IGNORECASE),
)
_SQL_START = re.compile(r"^\s*(?:with\b.*?\bselect\b|select\b.*?\bfrom\b|insert\s+(?:all\s+|or\s+\w+\s+)?into\b|"
                        r"update\s+\S+\s+set\b|delete\s+from\b|merge\s+into\b|replace\s+into\b|truncate\s+table\b)",
                        re.IGNORECASE | re.DOTALL)
_NOT_TABLES = {"dual", "select", "lateral", "unnest", "values", "only", "set", "where", "table"}
_NESTED_FUNCTIONS = re.compile(r"\b(?:EXTRACT|SUBSTRING|TRIM|OVERLAY)\s*\([^()]*\)", re.IGNORECASE)
_LITERAL_TABLE = re.compile(r"^[A-Z][A-Z0-9_]{2,}(?:\.[A-Z][A-Z0-9_]+)?$")
_SPRING_DATA = re.compile(
    r"\b(JpaRepository|CrudRepository|PagingAndSortingRepository|ListCrudRepository|ListPagingAndSortingRepository|"
    r"MongoRepository|ReactiveCrudRepository|R2dbcRepository|PanacheRepositoryBase|PanacheRepository|"
    r"PanacheMongoRepository|ElasticsearchRepository)\s*<\s*([\w.]+)")
_SQS_V2 = re.compile(r"\b(SendMessage|SendMessageBatch|ReceiveMessage|DeleteMessage|DeleteMessageBatch)Request\s*\.builder\s*\(")
_SQS_METHOD = re.compile(r"\.(sendMessage|sendMessageBatch|receiveMessage|deleteMessage|deleteMessageBatch)\s*\(")
_SQS_V1 = re.compile(r"\bnew\s+(SendMessage|SendMessageBatch|ReceiveMessage|DeleteMessage|DeleteMessageBatch)Request\s*\(")
_SQS_PRODUCERS = {"SendMessage", "SendMessageBatch", "sendMessage", "sendMessageBatch"}
_S3_REQUESTS = {
    "PutObject": "producer", "DeleteObject": "producer", "DeleteObjects": "producer", "CreateMultipartUpload": "producer",
    "UploadPart": "producer", "CompleteMultipartUpload": "producer", "PutObjectTagging": "producer",
    "GetObject": "consumer", "HeadObject": "consumer", "ListObjectsV2": "consumer", "ListObjects": "consumer",
    "GetObjectTagging": "consumer", "CopyObject": "producer",
}
_S3_REQUEST = re.compile(r"\b(" + "|".join(sorted(_S3_REQUESTS, key=len, reverse=True)) + r")Request\s*\.builder\s*\(")
_S3_METHODS = {
    "putObject": "producer", "deleteObject": "producer", "deleteObjects": "producer", "copyObject": "producer",
    "getObject": "consumer", "getObjectAsBytes": "consumer", "headObject": "consumer", "listObjectsV2": "consumer",
    "listObjects": "consumer",
}
_S3_METHOD = re.compile(r"\.(" + "|".join(_S3_METHODS) + r")\s*\(")
_S3_TEMPLATE = {"upload": "producer", "store": "producer", "deleteObject": "producer",
                "download": "consumer", "read": "consumer", "objectExists": "consumer"}
MAX_BINDING_TEXT = 80
SOURCE_ROOTS = (("src", "main", "java"), ("src", "main", "kotlin"))


def _is_source_path(relative):
    parts = Path(relative).parts
    return Path(relative).suffix in {".java", ".kt"} and any(
        parts[index:index + 3] in SOURCE_ROOTS for index in range(len(parts) - 2))


def _compact(text):
    text = " ".join(text.split())
    return text if len(text) <= MAX_BINDING_TEXT else text[:MAX_BINDING_TEXT - 3] + "..."


def _binding(form, reference, via=None):
    return {"form": form, "reference": mask_account_ids(reference), "via": via}


class _Source:
    def __init__(self, relative, text):
        self.relative = relative
        self.clean, self.code, self.strings = scan(text)
        self.starts = line_starts(text)
        self.found = annotations(self.code)
        self.names = {}
        for pattern in (_VALUE, _CONFIG_PROPERTY):
            for match in pattern.finditer(self.clean):
                self.names[match.group(2)] = match.group(1).strip()
        for match in _ALIAS.finditer(self.code):
            if match.group(2) in self.names:
                self.names.setdefault(match.group(1), self.names[match.group(2)])
        for match in _ASSIGNMENT.finditer(self.clean):
            read = _CONFIG_READ.match(match.group(2))
            if read:
                self.names.setdefault(match.group(1), read.group(1))
        self.constants = {match.group(1): match.group(2) for match in _CONSTANT.finditer(self.clean)}
        self.methods = []
        for match in _METHOD.finditer(self.code):
            if match.group(1) in _NOT_METHODS:
                continue
            brace = match.end() - 1
            end = group_end(self.code, brace)
            if end > 0:
                self.methods.append((match.group(1), self.parameters(match.group(2)), brace, end))
        self.field_parameters = self._field_parameters()

    def _field_parameters(self):
        """Map a field to the (constructor name, parameter index) that assigns it from a parameter."""
        result = {}
        for name, parameters, brace, end in self.methods:
            for match in re.finditer(r"\bthis\.(\w+)\s*=\s*(\w+)\s*;", self.code[brace:end]):
                if match.group(2) in parameters:
                    result[match.group(1)] = (name, parameters.index(match.group(2)))
        return result

    @staticmethod
    def parameters(text):
        names = []
        for segment in re.split(r",(?![^<]*>)", text):
            segment = segment.split("=")[0].strip()
            words = re.findall(r"\w+", segment.split(":")[0] if ":" in segment else segment)
            if words:
                names.append(words[-1])
        return names

    def line(self, offset):
        return line_of(self.starts, offset)

    def arguments(self, open_index):
        spans, close = split_arguments(self.code, open_index)
        if spans is None:
            return None, -1
        return [self.clean[start:end].strip() for start, end in spans], close

    def enclosing(self, offset):
        best = None
        for method in self.methods:
            if method[2] < offset < method[3] and (best is None or method[2] > best[2]):
                best = method
        return best


class IoPointsDetector:
    name = "io-points"
    version = 2

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
                if path.is_symlink() or not _is_source_path(relative.as_posix()):
                    continue
                try:
                    size = path.stat().st_size
                except OSError:
                    continue
                if exclusion_reason(relative, size) is None and size <= MAX_FILE_BYTES:
                    yield path, relative.as_posix()

    def detect(self, root, repository, commit):
        sources = []
        for path, relative in self._candidates(root):
            try:
                sources.append(_Source(relative, path.read_text(encoding="utf-8")))
            except (OSError, UnicodeError):
                continue
        self._environment_helpers(sources)
        self._providers = self._provider_methods(sources)
        records = self._record_components(sources)
        points, gaps = {}, {}
        wrappers = {}
        for source in sources:
            self._scan_file(source, records, points, gaps, wrappers)
        self._scan_wrappers(sources, records, points, wrappers)
        observations = []
        for (path, start, end, role, technology, construct, form, reference, via), _ in sorted(points.items()):
            observations.append(observation(
                kind="io-point", repository=repository, commit=commit, detector=self.name,
                detector_version=self.version, source_path=path,
                source_lines=str(start) if start == end else "{}-{}".format(start, end),
                value={"technology": technology, "role": role, "construct": construct,
                       "binding": _binding(form, reference, via)}))
        for (path, start, code, detail), _ in sorted(gaps.items()):
            observations.append(observation(
                kind="io-gap", repository=repository, commit=commit, detector=self.name,
                detector_version=self.version, source_path=path, source_lines=str(start),
                value={"code": code, "detail": detail}))
        return observations, []

    @staticmethod
    def _provider_methods(sources):
        """Map parameterless methods that return a configuration read to its key; ambiguous names are dropped."""
        found, ambiguous = {}, set()
        for source in sources:
            for name, parameters, brace, end in source.methods:
                read = re.match(r"\{\s*return\s+(.*?);", source.clean[brace:end + 1], re.DOTALL)
                match = _CONFIG_READ.match(read.group(1).strip()) if read and not parameters else None
                if match:
                    if found.setdefault(name, match.group(1)) != match.group(1):
                        ambiguous.add(name)
        return {name: key for name, key in found.items() if name not in ambiguous}

    @staticmethod
    def _environment_helpers(sources):
        """Bind variables assigned from helper methods that read an environment variable named by their argument."""
        helpers = set()
        for source in sources:
            for name, parameters, brace, end in source.methods:
                read = re.search(r"System\.getenv\(\s*(\w+)\s*\)", source.clean[brace:end])
                if read and read.group(1) in parameters:
                    helpers.add(name)
        for source in sources:
            for helper in sorted(helpers):
                for match in re.finditer(r'\b(\w+)\s*=\s*(?:\w+\.)?' + re.escape(helper) + r'\(\s*"([^"]+)"', source.clean):
                    source.names.setdefault(match.group(1), match.group(2))

    # -- records: constructor arguments bound to configuration keys ------------------------------------------

    def _record_components(self, sources):
        declared = {}
        for source in sources:
            for pattern in (_RECORD, _DATA_CLASS):
                for match in pattern.finditer(source.code):
                    arguments, _ = source.arguments(match.end() - 1)
                    if arguments:
                        declared[match.group(1)] = source.parameters(", ".join(arguments))
        bound = {}
        for source in sources:
            for kind, name, offset, attached in declarations(source.code, source.found):
                prefix = None
                for annotation in attached:
                    if annotation.name == "ConfigurationProperties" and annotation.open_index is not None:
                        arguments, _ = source.arguments(annotation.open_index)
                        match = _CONFIGURATION_PROPERTIES.match(arguments[0]) if arguments else None
                        if match:
                            prefix = match.group(1)
                if prefix is None:
                    continue
                brace = source.code.find("{", offset)
                end = group_end(source.code, brace) if brace >= 0 else -1
                fields = set(declared.get(name, []))
                if end > 0:
                    fields |= {match.group(1) for match in _FIELD.finditer(source.code[brace:end])}
                for field in fields:
                    bound.setdefault((name + "@properties", field), set()).add(prefix + "." + field)
            for name, components in declared.items():
                for match in re.finditer(r"\b(?:new\s+)?" + re.escape(name) + r"\s*\(", source.code):
                    arguments, _ = source.arguments(match.end() - 1)
                    if not arguments or len(arguments) != len(components):
                        continue
                    for component, argument in zip(components, arguments):
                        key = source.names.get(argument)
                        if key is not None:
                            bound.setdefault((name, component), set()).add(key)
        by_component = {}
        for (name, component), keys in bound.items():
            by_component.setdefault(component, {})[name] = keys
        return by_component

    # -- binding classification -----------------------------------------------------------------------------

    def _bind(self, source, argument, records):
        argument = argument.strip()
        match = _STRING.match(argument)
        if match:
            placeholder = _PLACEHOLDER.match(match.group(1).replace("\\${", "${"))
            if placeholder:
                return _binding("config-key", placeholder.group(1).strip(), "placeholder")
            if "${" in match.group(1):
                return _binding("unresolved", _compact(argument), "interpolated-string")
            return _binding("literal", match.group(1))
        match = _IDENTIFIER.match(argument)
        if match:
            name = match.group(1)
            if name in source.names:
                return _binding("config-key", source.names[name], "parameter {}".format(name))
            if name in source.constants:
                return _binding("literal", source.constants[name], "constant {}".format(name))
            if name in self._providers:
                return _binding("config-key", self._providers[name], "provider method {}() matched by name".format(name))
            return _binding("unresolved", name, "identifier")
        read = _CONFIG_READ.match(argument)
        if read:
            return _binding("config-key", read.group(1), "configuration read")
        match = _ACCESSOR.match(argument)
        if match:
            name = match.group(1) or match.group(2)
            for candidate in (name, name[:1].lower() + name[1:]):
                if candidate in records:
                    owners = records[candidate]
                    keys = {key for keys in owners.values() for key in keys}
                    if len(keys) == 1:
                        owner = sorted(owners)[0]
                        kind = "configuration class" if owner.endswith("@properties") else "record"
                        return _binding("config-key", next(iter(keys)),
                                        "{} {}.{}".format(kind, owner.replace("@properties", ""), candidate))
        return _binding("unresolved", _compact(argument), "expression")

    # -- per-file scanning ------------------------------------------------------------------------------------

    def _scan_file(self, source, records, points, gaps, wrappers):
        def point(start, end, technology, role, construct, binding):
            first, last = source.line(start), source.line(max(start, end - 1))
            points[(source.relative, first, last, role, technology, construct, binding["form"],
                    binding["reference"], binding["via"])] = True
            return first

        def gap(offset, code, detail):
            gaps[(source.relative, source.line(offset), code, detail)] = True

        def bind_each(start, end, technology, role, construct, argument, wrappable=True):
            elements = [argument]
            if argument.startswith("{") and argument.endswith("}"):
                elements = [item.strip() for item in re.split(r",(?![^(]*\))", argument[1:-1]) if item.strip()]
            for element in elements:
                binding = self._bind(source, element, records)
                point(start, end, technology, role, construct, binding)
                if wrappable and binding["form"] == "unresolved" and binding["via"] == "identifier":
                    method = source.enclosing(start)
                    if method is not None and binding["reference"] in method[1]:
                        wrappers.setdefault(method[0], set()).add(
                            (method[1].index(binding["reference"]), technology, role, construct))
                    elif binding["reference"] in source.field_parameters:
                        owner, index = source.field_parameters[binding["reference"]]
                        wrappers.setdefault(owner, set()).add((index, technology, role, construct))

        text = source.clean
        self._messaging_channels(source, text, point, bind_each)
        self._kafka(source, text, bind_each, gap)
        self._sqs(source, text, bind_each)
        self._s3(source, text, bind_each)
        self._amqp(source, text, bind_each, gap)
        self._persistence(source, point)

    def _messaging_channels(self, source, text, point, bind_each):
        for pattern, role in ((r'@Incoming\s*\(\s*"([^"]+)"\s*\)', "consumer"),
                              (r'@Outgoing\s*\(\s*"([^"]+)"\s*\)', "producer"),
                              (r'@Channel\s*\(\s*"([^"]+)"\s*\)\s*(?:@\w+\s+)*(?:final\s+)?(?:Mutiny)?Emitter\b', "producer")):
            for match in re.finditer(pattern, text):
                construct = "@" + match.group(0).split("(")[0].lstrip("@").strip()
                point(match.start(), match.end(), "messaging", role, construct, _binding("channel", match.group(1)))
        if "StreamBridge" in text:
            for match in re.finditer(r'\.send\s*\(\s*"([^"]+)"', text):
                point(match.start(), match.end(), "messaging", "producer", "StreamBridge.send",
                      _binding("channel", match.group(1)))

    def _kafka(self, source, text, bind_each, gap):
        uses_streams = "org.apache.kafka.streams" in text
        uses_clients = "org.apache.kafka.clients" in text or "org.springframework.kafka" in text
        for match in re.finditer(r"@KafkaListener\s*\(", source.code):
            arguments, close = source.arguments(match.end() - 1)
            end = close + 1
            topics = None
            for argument in arguments or []:
                named = re.match(r"(topics|topicPattern|value)\s*=\s*(.*)$", argument, re.DOTALL)
                if named:
                    topics = (named.group(1), named.group(2).strip())
            if topics is None:
                gap(match.start(), "unsupported-listener-form", "The @KafkaListener topics are not declared with topics.")
            elif topics[0] == "topicPattern":
                bind_each(match.start(), end, "kafka", "consumer", "@KafkaListener(topicPattern)", topics[1], False)
            else:
                bind_each(match.start(), end, "kafka", "consumer", "@KafkaListener", topics[1], False)
        if uses_streams:
            for match in re.finditer(r"\.(stream|table|globalTable)\s*\(\s*(?=[^)\s])", source.code):
                arguments, close = source.arguments(source.code.index("(", match.start()))
                if arguments and not re.match(r"^\w+\s*->|^\(", arguments[0]):
                    bind_each(match.start(), close + 1, "kafka", "consumer", "StreamsBuilder." + match.group(1), arguments[0])
            for match in re.finditer(r"\.(to|through)\s*\(", source.code):
                arguments, close = source.arguments(match.end() - 1)
                if arguments and not re.match(r"^\w+\s*->", arguments[0]):
                    bind_each(match.start(), close + 1, "kafka", "producer", "KStream." + match.group(1), arguments[0])
        if uses_clients:
            for match in re.finditer(r"\b(?:new\s+)?ProducerRecord\s*(?:<[^()]*?>)?\s*\(", source.code):
                arguments, close = source.arguments(match.end() - 1)
                if arguments:
                    bind_each(match.start(), close + 1, "kafka", "producer", "ProducerRecord", arguments[0])
            for match in re.finditer(r"\b(\w*(?:[kK]afka|[tT]emplate|[pP]roducer)\w*)\.(send|sendDefault)\s*\(", source.code):
                arguments, close = source.arguments(match.end() - 1)
                if match.group(2) == "sendDefault":
                    bind_each(match.start(), close + 1, "kafka", "producer", "KafkaTemplate.sendDefault",
                              '"${spring.kafka.template.default-topic}"', False)
                elif arguments and not arguments[0].startswith(("new ProducerRecord", "ProducerRecord")):
                    bind_each(match.start(), close + 1, "kafka", "producer", "KafkaTemplate.send", arguments[0])
            for match in re.finditer(r"\.subscribe\s*\(", source.code):
                arguments, close = source.arguments(match.end() - 1)
                if not arguments:
                    continue
                wrapped = re.match(r"^(?:List|Set|Arrays|Collections)\.(?:of|asList|singletonList|singleton)\s*\(", arguments[0])
                if wrapped:
                    inner, _ = source.arguments(source.code.index("(", match.end()))
                    elements = inner or []
                else:
                    elements = [arguments[0]]
                for element in elements:
                    bind_each(match.start(), close + 1, "kafka", "consumer", "KafkaConsumer.subscribe", element)

    def _sqs(self, source, text, bind_each):
        sdk = "software.amazon.awssdk.services.sqs" in text or "com.amazonaws.services.sqs" in text
        spring = "io.awspring" in text or "org.springframework.cloud.aws" in text
        if sdk:
            for match in _SQS_V2.finditer(source.code):
                end = statement_end(source.code, match.start())
                self._queue_url(source, match.start(), end, match.group(1), bind_each)
            for match in _SQS_METHOD.finditer(source.code):
                open_index = match.end() - 1
                close = group_end(source.code, open_index)
                segment = source.code[open_index:close + 1]
                if close > 0 and "->" in segment:
                    self._queue_url(source, match.start(), close, match.group(1), bind_each)
                elif close > 0 and "com.amazonaws.services.sqs" in text:
                    arguments, _ = source.arguments(open_index)
                    if arguments and not re.match(r"^(?:new\s+)?\w*Request\b", arguments[0]):
                        role = "producer" if match.group(1) in _SQS_PRODUCERS else "consumer"
                        bind_each(match.start(), close + 1, "sqs", role, "SqsClient." + match.group(1), arguments[0])
            for match in re.finditer(r"\bnew\s+(SendMessage|SendMessageBatch|ReceiveMessage|DeleteMessage|DeleteMessageBatch)Request\s*\(\s*\)",
                                     source.code):
                self._queue_url(source, match.start(), statement_end(source.code, match.start()), match.group(1), bind_each)
            for match in _SQS_V1.finditer(source.code):
                arguments, close = source.arguments(match.end() - 1)
                if arguments and "com.amazonaws.services.sqs" in text:
                    role = "producer" if match.group(1) in _SQS_PRODUCERS else "consumer"
                    bind_each(match.start(), close + 1, "sqs", role, match.group(1) + "Request", arguments[0])
        if spring:
            for match in re.finditer(r"@SqsListener\s*\(", source.code):
                arguments, close = source.arguments(match.end() - 1)
                for argument in arguments or []:
                    named = re.match(r"(?:value|queueNames)\s*=\s*(.*)$", argument, re.DOTALL)
                    bind_each(match.start(), close + 1, "sqs", "consumer", "@SqsListener",
                              named.group(1).strip() if named else argument, False)
            for match in re.finditer(r"\.(convertAndSend|send)\s*\(", source.code):
                arguments, close = source.arguments(match.end() - 1)
                if arguments and re.search(r"[sS]qs|[qQ]ueue", source.code[max(0, match.start() - 40):match.start()]):
                    inner = re.search(r"\.queue\s*\(([^)]*)\)", source.code[match.end():close])
                    argument = inner.group(1) if inner else arguments[0]
                    bind_each(match.start(), close + 1, "sqs", "producer", "SqsTemplate." + match.group(1), argument.strip())

    def _queue_url(self, source, start, end, kind, bind_each):
        role = "producer" if kind in _SQS_PRODUCERS else "consumer"
        for match in re.finditer(r"\.(?:queueUrl|withQueueUrl)\s*\(", source.code[start:end]):
            open_index = start + match.end() - 1
            arguments, close = source.arguments(open_index)
            if arguments:
                bind_each(start, close + 1, "sqs", role, kind + "Request.queueUrl", arguments[0])

    def _s3(self, source, text, bind_each):
        v2 = "software.amazon.awssdk.services.s3" in text
        v1 = "com.amazonaws.services.s3" in text
        if v2:
            for match in _S3_REQUEST.finditer(source.code):
                end = statement_end(source.code, match.start())
                segment = source.code[match.start():end]
                for name, role in (("bucket", _S3_REQUESTS[match.group(1)]), ("sourceBucket", "consumer"),
                                   ("destinationBucket", "producer")):
                    for call in re.finditer(r"\.{}\s*\(".format(name), segment):
                        arguments, close = source.arguments(match.start() + call.end() - 1)
                        if arguments:
                            bind_each(match.start(), close + 1, "s3", role, match.group(1) + "Request." + name, arguments[0])
            for match in _S3_METHOD.finditer(source.code):
                open_index = match.end() - 1
                close = group_end(source.code, open_index)
                if close > 0 and "->" in source.code[open_index:close]:
                    for call in re.finditer(r"\.bucket\s*\(", source.code[open_index:close]):
                        arguments, argument_close = source.arguments(open_index + call.end() - 1)
                        if arguments:
                            bind_each(match.start(), close + 1, "s3", _S3_METHODS[match.group(1)],
                                      "S3Client." + match.group(1) + ".bucket", arguments[0])
        if v1:
            for match in _S3_METHOD.finditer(source.code):
                arguments, close = source.arguments(match.end() - 1)
                if arguments and not re.match(r"^(?:new\s+)?\w*Request\b|^\w+\s*->|^\w+Request$", arguments[0]):
                    bind_each(match.start(), close + 1, "s3", _S3_METHODS[match.group(1)],
                              "AmazonS3." + match.group(1), arguments[0])
            for match in re.finditer(r"\bnew\s+(PutObject|GetObject|DeleteObject|ListObjects)Request\s*\(", source.code):
                arguments, close = source.arguments(match.end() - 1)
                if arguments:
                    bind_each(match.start(), close + 1, "s3", _S3_REQUESTS[match.group(1)],
                              match.group(1) + "Request", arguments[0])
        if "io.awspring.cloud.s3" in text:
            for match in re.finditer(r"\b(\w*[sS]3\w*)\.(upload|store|download|read|deleteObject|objectExists)\s*\(", source.code):
                arguments, close = source.arguments(match.end() - 1)
                if arguments:
                    bind_each(match.start(), close + 1, "s3", _S3_TEMPLATE[match.group(2)],
                              "S3Template." + match.group(2), arguments[0])

    def _amqp(self, source, text, bind_each, gap):
        if "org.springframework.amqp" not in text:
            return
        for match in re.finditer(r"@RabbitListener\s*\(", source.code):
            arguments, close = source.arguments(match.end() - 1)
            queue = None
            for argument in arguments or []:
                named = re.match(r"queues\s*=\s*(.*)$", argument, re.DOTALL)
                if named:
                    queue = named.group(1).strip()
            declared = re.search(r'@Queue\s*\(\s*(?:name\s*=\s*|value\s*=\s*)?"([^"]+)"', source.clean[match.start():close + 1])
            if declared:
                queue = '"{}"'.format(declared.group(1))
            if queue is None:
                gap(match.start(), "unsupported-listener-form", "The @RabbitListener queues are not declared with queues.")
            else:
                bind_each(match.start(), close + 1, "amqp", "consumer", "@RabbitListener", queue, False)
        for match in re.finditer(r"\b(\w*(?:[rR]abbit|[aA]mqp)\w*)\.(convertAndSend|send|convertSendAndReceive)\s*\(", source.code):
            arguments, close = source.arguments(match.end() - 1)
            if arguments:
                bind_each(match.start(), close + 1, "amqp", "producer", "RabbitTemplate." + match.group(2), arguments[0])

    # -- persistence ----------------------------------------------------------------------------------------

    def _persistence(self, source, point):
        consumed = []
        repository_class = False
        for kind, name, offset, attached in declarations(source.code, source.found):
            names = {item.name: item for item in attached}
            brace = source.code.find("{", offset)
            declaration_end = brace if brace >= 0 else offset + 200
            header = source.code[offset:declaration_end]
            first = min([item.start for item in attached] + [offset])
            if "Repository" in names or name.endswith(("Repository", "Dao", "Persistence")):
                repository_class = True
            if "Repository" in names:
                point(first, declaration_end, "persistence", "declaration", "@Repository", _binding("type", name))
            spring_data = _SPRING_DATA.search(header)
            if spring_data:
                point(first, declaration_end, "persistence", "declaration", "extends " + spring_data.group(1),
                      _binding("type", name, "entity " + spring_data.group(2).split(".")[-1]))
            if "Table" in names or "Entity" in names:
                table = self._table_name(source, names.get("Table"))
                if table is not None:
                    point(first, declaration_end, "persistence", "mapped", "@Table", _binding("literal", table))
                elif "Entity" in names:
                    point(first, declaration_end, "persistence", "mapped", "@Entity", _binding("type", name))
            document = names.get("Document")
            if document is not None and document.open_index is not None:
                arguments, _ = source.arguments(document.open_index)
                for argument in arguments or []:
                    named = re.match(r"(?:collection\s*=\s*)?(\".*\")$", argument, re.DOTALL)
                    if named:
                        point(document.start, document.end, "persistence", "mapped", "@Document",
                              self._bind(source, named.group(1), {}))
        for annotation in source.found:
            if annotation.name == "Query" and annotation.open_index is not None:
                arguments, _ = source.arguments(annotation.open_index)
                if not arguments:
                    continue
                native = any(re.match(r"nativeQuery\s*=\s*true", item) for item in arguments)
                value = re.sub(r"^value\s*=\s*", "", arguments[0])
                match = _STRING.match(value.strip()) if value.strip().startswith('"') and "+" not in value else None
                text = match.group(1) if match else None
                if text is None:
                    text = "".join(literal.value for literal in source.strings
                                   if annotation.start <= literal.start < annotation.end)
                consumed.append((annotation.start, annotation.end))
                self._statement(source, text, annotation.start, annotation.end, point, native, "@Query")
        if repository_class:
            for match in re.finditer(r"\b(?:super|this)\.\w+\s*\(", source.code):
                arguments, close = source.arguments(match.end() - 1)
                for argument in arguments or []:
                    literal = _STRING.match(argument)
                    if literal and _LITERAL_TABLE.match(literal.group(1)):
                        point(match.start(), close + 1, "persistence", "mapped", "repository-literal-argument",
                              _binding("literal", literal.group(1)))
        for start, end, text in self._statements(source, consumed):
            self._statement(source, text, start, end, point, True, "sql")

    def _table_name(self, source, annotation):
        if annotation is None or annotation.open_index is None:
            return None
        arguments, _ = source.arguments(annotation.open_index)
        table = schema = None
        for argument in arguments or []:
            named = re.match(r'(name|value|schema)\s*=\s*"([^"]+)"$', argument) or re.match(r'()"([^"]+)"$', argument)
            if named and named.group(1) == "schema":
                schema = named.group(2)
            elif named:
                table = named.group(2)
        return None if table is None else (schema + "." + table if schema else table)

    def _statements(self, source, consumed):
        groups, current = [], []
        for literal in source.strings:
            if any(start <= literal.start < end for start, end in consumed):
                continue
            joined = current and re.match(r"^\s*\+\s*$", source.code[current[-1].end:literal.start])
            if current and not joined:
                groups.append(current)
                current = []
            current.append(literal)
        if current:
            groups.append(current)
        for group in groups:
            text = " ".join(item.value for item in group)
            if _SQL_START.match(" ".join(text.split())):
                yield group[0].start, group[-1].end, text

    def _statement(self, source, text, start, end, point, native, construct):
        text = " ".join(text.split())
        if not _SQL_START.match(text):
            return
        form = "literal" if native else "type"
        text = _NESTED_FUNCTIONS.sub(" ", text)
        written = set()
        for pattern in _WRITES:
            for match in pattern.finditer(text):
                written.add(match.start(1))
                self._table(point, start, end, "writer", construct, match.group(1), form)
        for pattern in _READS:
            for match in pattern.finditer(text):
                if match.start(1) not in written:
                    self._table(point, start, end, "reader", construct, match.group(1), form)

    @staticmethod
    def _table(point, start, end, role, construct, token, form):
        token = token.strip('"`')
        if token.lower() in _NOT_TABLES or token.isdigit():
            return
        placeholder = re.match(r"^\$(\w+)\$$|^\$\{([^}]+)\}$", token)
        if placeholder:
            point(start, end, "persistence", role, construct, _binding("placeholder", placeholder.group(1) or placeholder.group(2)))
        elif token == "%s":
            point(start, end, "persistence", role, construct, _binding("placeholder", "%s"))
        else:
            point(start, end, "persistence", role, construct, _binding(form, token))

    # -- wrappers: methods that forward a parameter to a messaging call --------------------------------------

    def _scan_wrappers(self, sources, records, points, wrappers):
        declared = {}
        for source in sources:
            for name, _, _, _ in source.methods:
                declared[name] = declared.get(name, 0) + 1
        for name, entries in sorted(wrappers.items()):
            if declared.get(name) != 1:
                continue
            for source in sources:
                for match in re.finditer(r"(?<![\w])" + re.escape(name) + r"\s*\(", source.code):
                    if any(method[0] == name and method[2] - 400 < match.start() < method[2] for method in source.methods):
                        continue
                    arguments, close = source.arguments(match.end() - 1)
                    if arguments is None:
                        continue
                    for index, technology, role, construct in sorted(entries):
                        if index >= len(arguments):
                            continue
                        binding = self._bind(source, arguments[index], records)
                        if binding["form"] == "unresolved":
                            continue
                        first = source.line(match.start())
                        last = source.line(max(match.start(), close))
                        points[(source.relative, first, last, role, technology, construct + " via " + name,
                                binding["form"], binding["reference"], binding["via"])] = True
