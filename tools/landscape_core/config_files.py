"""Deterministic reading and masking of application configuration files."""

from pathlib import PurePosixPath
import re


PROFILE_NAMES = {"local", "dev", "development", "test", "int", "pre", "preprod", "staging", "pro", "prod", "production"}
EXTENSIONS = {".properties", ".yaml", ".yml"}
SENSITIVE_KEY = re.compile(
    r"pass(word|wd)?|pwd|secret|token|credential|private[-_.]?key|api[-_.]?key|access[-_.]?key",
    re.IGNORECASE,
)
ACCOUNT_MASK = "<aws-account>"
WITHHELD_MASK = "<withheld>"
_ACCOUNT_PATTERNS = (
    re.compile(r"(amazonaws\.com(?::\d+)?/)(\d{12})(?!\d)"),
    re.compile(r"(localhost(?::\d+)?/)(\d{12})(?!\d)"),
    re.compile(r"(arn:aws[a-z-]*:[a-z0-9-]*:[a-z0-9-]*:)(\d{12})(?!\d)"),
    re.compile(r"(account[-_.]?id\s*[=:]\s*)(\d{12})(?!\d)", re.IGNORECASE),
    re.compile(r"(?<!\d)()(\d{12})(?=\.dkr\.ecr[\w.-]*\.amazonaws\.com)"),
)
_YAML_KEY = re.compile(r"""^(?P<key>"[^"]+"|'[^']+'|[^:#\s][^:#]*?)\s*:(?:\s+(?P<value>.*))?$""")
_PROPERTY = re.compile(r"^(?P<key>[^=:\s]+)(?:\s*[=:]\s*|\s+)?(?P<value>.*)$")
_UNSUPPORTED_YAML_VALUE = re.compile(r"^(?:[|>][+-]?\d*|[&*!].*|[\[{].*)$")


def is_config_path(relative):
    """Return whether a repository-relative path is a main-source configuration file."""
    path = PurePosixPath(relative)
    parts = path.parts
    in_resources = any(parts[index:index + 3] == ("src", "main", "resources") for index in range(len(parts) - 2))
    return in_resources and path.suffix.lower() in EXTENSIONS


def file_profile(relative):
    """Return the profile named by a file suffix such as application-pro.properties, if known."""
    stem = PurePosixPath(relative).stem
    _, separator, token = stem.rpartition("-")
    return token.lower() if separator and token.lower() in PROFILE_NAMES else None


def mask_account_ids(text):
    for pattern in _ACCOUNT_PATTERNS:
        text = pattern.sub(lambda match: match.group(1) + ACCOUNT_MASK, text)
    return text


def _unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _strip_comment(value):
    if value[:1] in "\"'":
        return value
    position = value.find(" #")
    return value[:position].rstrip() if position >= 0 else value


def _split_profile(key):
    """Split a Quarkus-style '%profile.key' prefix; return (profile, key) or (None, None) if unsupported."""
    if not key.startswith("%"):
        return None, key
    profile, _, rest = key[1:].partition(".")
    if not profile or not rest or "," in profile:
        return None, None
    return profile, rest


def _parse_properties(text):
    entries, gaps = [], []
    continued = False
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if continued:
            continued = stripped.endswith("\\")
            continue
        if not stripped or stripped[0] in "#!":
            continue
        if stripped.endswith("\\") and (len(stripped) - len(stripped.rstrip("\\"))) % 2:
            gaps.append({"line": number, "code": "unsupported-config-syntax",
                         "detail": "Line continuations in properties files are not interpreted."})
            continued = True
            continue
        match = _PROPERTY.match(stripped)
        profile, key = _split_profile(match.group("key"))
        if key is None:
            gaps.append({"line": number, "code": "unsupported-profile-expression",
                         "detail": "Profile expressions other than a single %profile prefix are not interpreted."})
            continue
        entries.append({"key": key, "profile": profile, "value": match.group("value").strip(),
                        "line": number, "raw": raw})
    return entries, gaps


def _parse_yaml(text):
    entries, gaps = [], []
    stack = []  # (indent, key)
    counters = {}
    skip_indent = None
    documents = 0
    for number, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "\t" in raw[:len(raw) - len(raw.lstrip())]:
            gaps.append({"line": number, "code": "unsupported-config-syntax",
                         "detail": "Tab indentation in YAML files is not interpreted."})
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if skip_indent is not None:
            if indent > skip_indent:
                continue
            skip_indent = None
        if stripped == "---":
            documents += 1
            if documents > 1:
                gaps.append({"line": number, "code": "unsupported-multi-document",
                             "detail": "Only the first YAML document is interpreted."})
                break
            continue
        if stripped == "...":
            break
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stripped.startswith("- "):
            if not stack:
                continue
            item = _strip_comment(stripped[2:].strip())
            if _YAML_KEY.match(item) or _UNSUPPORTED_YAML_VALUE.match(item):
                gaps.append({"line": number, "code": "unsupported-yaml-construct",
                             "detail": "Only scalar YAML list items are interpreted."})
                continue
            parent = ".".join(key for _, key in stack)
            index = counters.get(parent, 0)
            counters[parent] = index + 1
            entries.append(_yaml_entry(stack, "{}[{}]".format(parent, index), _unquote(item), number, raw, True))
            continue
        match = _YAML_KEY.match(stripped)
        if match is None:
            gaps.append({"line": number, "code": "unsupported-config-syntax",
                         "detail": "The YAML line is not a supported key/value or nesting form."})
            continue
        key = _unquote(match.group("key").strip())
        value = _strip_comment((match.group("value") or "").strip())
        if not value:
            stack.append((indent, key))
            counters.pop(".".join(item_key for _, item_key in stack), None)
            continue
        if _UNSUPPORTED_YAML_VALUE.match(value):
            gaps.append({"line": number, "code": "unsupported-yaml-construct",
                         "detail": "Block scalars, anchors, tags, and flow collections are not interpreted."})
            if value[0] in "|>":
                skip_indent = indent
            continue
        entries.append(_yaml_entry(stack, key, _unquote(value), number, raw, False))
    return entries, gaps


def _yaml_entry(stack, key, value, number, raw, is_item):
    path = [item_key for _, item_key in stack]
    if is_item:
        full = key
    else:
        full = ".".join(path + [key])
    profile = None
    if full.startswith("%"):
        profile, _, full = full[1:].partition(".")
        profile = profile or None
    return {"key": full, "profile": profile, "value": value, "line": number, "raw": raw}


def parse_config(text, relative):
    """Return (entries, gaps) for one configuration file; each entry carries key, profile, value, line."""
    if PurePosixPath(relative).suffix.lower() == ".properties":
        return _parse_properties(text)
    return _parse_yaml(text)


def classify_resource(key, value):
    """Name the infrastructure resource a key or value looks like, or None. Never infers business meaning."""
    lowered_key, lowered_value = key.lower(), value.lower()
    if lowered_key.startswith(("quarkus.log.", "logging.")):
        return None
    if lowered_value.startswith("jdbc:") or "jdbc" in lowered_key or "datasource" in lowered_key:
        return "jdbc"
    if "sqs" in lowered_key or ("amazonaws.com" in lowered_value and "sqs" in lowered_value):
        return "sqs"
    if "schema.registry" in lowered_key or "schema-registry" in lowered_key or "schemaregistry" in lowered_key:
        return "schema-registry"
    if "kafka" in lowered_key and "topic" in lowered_key or lowered_key.endswith(".topic") or ".topic." in lowered_key:
        return "kafka-topic"
    if "kafka" in lowered_key or "bootstrap.servers" in lowered_key or "bootstrap-servers" in lowered_key:
        return "kafka"
    if "rabbit" in lowered_key or "amqp" in lowered_key or lowered_value.startswith("amqp"):
        return "amqp"
    return None


def describe_value(key, value):
    """Return (disposition, stored value) with secrets withheld and account ids masked."""
    if "${" in value:
        return "placeholder", mask_account_ids(value)
    if not value:
        return "empty", ""
    if SENSITIVE_KEY.search(key):
        return "withheld", None
    return "literal", mask_account_ids(value)


def mask_config_text(text, relative):
    """Return the file text with sensitive values withheld and account ids masked, line for line."""
    entries, _ = parse_config(text, relative)
    lines = text.split("\n")
    for entry in entries:
        disposition, _ = describe_value(entry["key"], entry["value"])
        index = entry["line"] - 1
        if disposition == "withheld" and entry["value"] and index < len(lines):
            position = lines[index].rfind(entry["value"])
            if position >= 0:
                lines[index] = lines[index][:position] + WITHHELD_MASK + lines[index][position + len(entry["value"]):]
    return mask_account_ids("\n".join(lines))
