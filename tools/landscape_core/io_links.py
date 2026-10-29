"""Resolve I/O points against configuration and pair producers with consumers across repositories."""

import re
from pathlib import PurePosixPath

from .env_vars import environment_names, manifest_profile

LOCAL_PROFILES = {"local", "test"}
_PLACEHOLDER = re.compile(r"^\$\{([^}:]+)(?::(.*))?\}$")
TECHNOLOGY_RESOURCES = {"kafka": "kafka-topic", "sqs": "sqs-queue", "s3": "s3-bucket", "amqp": "amqp-destination"}
CONNECTOR_RESOURCES = (("kafka", "kafka-topic", "topic"), ("amqp", "amqp-destination", "address"),
                       ("rabbit", "amqp-destination", "address"))
DIRECTION = {"producer": "outgoing", "consumer": "incoming"}
FLOW_ROLES = {"producer": "producer", "consumer": "consumer", "writer": "producer", "reader": "consumer"}


class Resolved:
    """An I/O point whose target was resolved to comparable identities per configuration profile."""

    def __init__(self, point, resource, by_profile, config_items, note=None, curated=None):
        self.point = point
        self.resource = resource
        self.by_profile = by_profile
        self.config_items = config_items
        self.note = note
        self.curated = curated or []

    @property
    def repository(self):
        return self.point["repository"]

    @property
    def role(self):
        return self.point["value"]["role"]

    def identity(self, profile):
        return self.by_profile.get(profile, self.by_profile.get(None))


def _configuration(inventory):
    index = {}
    for item in inventory["observations"]:
        value = item.get("value", {})
        if item["kind"] == "config-property" and value["disposition"] == "literal":
            index.setdefault(value["key"], []).append(item)
    return index


def _environment(inventories, pairing=None):
    """Map each application repository to the literal environment variables of its manifests, by name.

    A selection is paired through a topology binding when one exists, else with the repository named
    like the last folder of its subpath.
    """
    result = {}
    for inventory in inventories:
        selection = inventory.get("sourceSelection")
        if not selection or selection.get("kind") != "kubernetes":
            continue
        repositories = (pairing or {}).get(selection["id"], ({PurePosixPath(selection["subpath"]).name}, False))[0]
        for item in inventory["observations"]:
            if item["kind"] == "env-variable" and item["value"]["disposition"] == "literal":
                for repository in repositories:
                    result.setdefault(repository, {}).setdefault(item["value"]["name"], []).append(item)
    return result


def _manifest_profile(item):
    return manifest_profile(item["source"]["path"])


def key_variants(key):
    """The property key plus its kebab-case spelling, as Spring and Quarkus relaxed binding accept both."""
    kebab = ".".join(re.sub(r"(?<!^)(?=[A-Z])", "-", segment).lower() for segment in key.split("."))
    return [key] if kebab == key else [key, kebab]


def _values(index, key, env):
    """Return ({profile: value}, items) for a key from properties, resolving env placeholders and overrides."""
    by_profile, items = {}, []
    entries = [entry for variant in key_variants(key) for entry in index.get(variant, [])]
    for item in sorted(entries, key=lambda entry: (entry["source"]["path"], entry["value"]["line"])):
        value = item["value"]
        if value["disposition"] == "literal":
            by_profile[value["profile"]] = value["value"]
            items.append(item)
            continue
        placeholder = _PLACEHOLDER.match(value["value"] or "")
        if value["disposition"] != "placeholder" or placeholder is None:
            continue
        found = False
        for entry in env.get(placeholder.group(1).strip(), []):
            profile = value["profile"] or _manifest_profile(entry)
            if value["profile"] and _manifest_profile(entry) not in {None, value["profile"]}:
                continue
            by_profile[profile] = entry["value"]["value"]
            items.append(entry)
            found = True
        if not found and placeholder.group(2) is not None:
            by_profile[value["profile"]] = placeholder.group(2)
            items.append(item)
    for name in sorted({name for variant in key_variants(key) for name in environment_names(variant)}):
        for entry in env.get(name, []):
            by_profile[_manifest_profile(entry)] = entry["value"]["value"]
            items.append(entry)
    return by_profile, items


def _identity(resource, value):
    value = value.strip()
    if resource == "sqs-queue":
        return value.rstrip("/").rsplit("/", 1)[-1]
    if resource == "table":
        return value.strip('"`').upper()
    return value


def _resolve(point, index, env):
    value = point["value"]
    binding = value["binding"]
    technology, form = value["technology"], binding["form"]
    if value["technology"] == "persistence":
        if form == "literal" and value["role"] in {"reader", "writer", "mapped"}:
            return Resolved(point, "table", {None: _identity("table", binding["reference"])}, [])
        return None
    resource = TECHNOLOGY_RESOURCES.get(technology)
    if form == "literal" and resource is not None:
        return Resolved(point, resource, {None: _identity(resource, binding["reference"])}, [])
    if form == "config-key" and resource is not None:
        values, items = _values(index, binding["reference"], env)
        if values:
            return Resolved(point, resource, {profile: _identity(resource, text) for profile, text in values.items()},
                            items, "queue name only" if resource == "sqs-queue" else None)
        return None
    if form == "channel" and technology == "messaging":
        prefix = "mp.messaging.{}.{}.".format(DIRECTION[value["role"]], binding["reference"])
        connectors, connector_items = _values(index, prefix + "connector", env)
        for needle, resource, key in CONNECTOR_RESOURCES:
            if any(needle in text for text in connectors.values()):
                values, items = _values(index, prefix + key, env)
                if values:
                    return Resolved(point, resource, {profile: _identity(resource, text) for profile, text in values.items()},
                                    connector_items + items)
                excluded = {prefix + "topics", prefix + "topic-pattern", prefix + "pattern"}
                if resource == "kafka-topic" and not any(name in index for name in excluded):
                    return Resolved(point, resource, {None: binding["reference"]}, connector_items,
                                    "channel name used as the topic")
    return None


def answered_targets(draft):
    """Map an observation id to (question, answer) for answered questions that carry per-environment values."""
    result = {}
    for question in (draft or {}).get("questions", []):
        if question.get("expects") == "target-values" and question["answerStatus"] == "answered":
            for link in question["links"]:
                if link["type"] == "observation":
                    result[link["observationId"]] = (question, question["answer"])
    return result


def _answered(point, answers):
    found = answers.get(point["id"])
    resource = TECHNOLOGY_RESOURCES.get(point["value"]["technology"])
    if found is None or resource is None:
        return None
    question, answer = found
    by_profile = {entry["profile"]: _identity(resource, entry["value"]) for entry in answer["values"]}
    curated = {"kind": "curated", "sourceId": question["id"], "sourceType": "interview",
               "suppliedBy": answer["answerer"], "recordedAt": answer["recordedAt"]}
    return Resolved(point, resource, by_profile, [], curated=[curated])


def resolve_points(inventories, pairing=None, draft=None):
    resolved = []
    environment = _environment(inventories, pairing)
    answers = answered_targets(draft)
    for inventory in inventories:
        index = _configuration(inventory)
        for item in inventory["observations"]:
            if item["kind"] == "io-point":
                result = _resolve(item, index, environment.get(inventory["repository"], {})) or _answered(item, answers)
                if result is not None:
                    resolved.append(result)
    return resolved


def unresolved_points(inventories, pairing=None, draft=None):
    """Messaging and storage points whose target neither code, configuration, manifests, nor an answer settles."""
    environment = _environment(inventories, pairing)
    answers = answered_targets(draft)
    result = []
    for inventory in inventories:
        index = _configuration(inventory)
        for item in inventory["observations"]:
            value = item["value"] if item["kind"] == "io-point" else {}
            if value.get("technology") in TECHNOLOGY_RESOURCES and value.get("role") in {"producer", "consumer"}:
                if _resolve(item, index, environment.get(inventory["repository"], {})) is None and item["id"] not in answers:
                    result.append(item)
    return result


def matching_profiles(first, second):
    """Return the shared-environment profiles in which two resolved points name the same target."""
    profiles = {profile for profile in set(first.by_profile) | set(second.by_profile) if profile not in LOCAL_PROFILES}
    matched = []
    for profile in sorted(profiles, key=lambda name: (name is not None, name or "")):
        left, right = first.identity(profile), second.identity(profile)
        if left is not None and left == right:
            matched.append(profile)
    return matched


def feed_matches(inventories, pairing=None, draft=None):
    """Return {(producer repository, consumer repository): [match]} for producers paired with consumers."""
    resolved = [item for item in resolve_points(inventories, pairing, draft) if item.role in {"producer", "consumer", "writer", "reader"}]
    result = {}
    for producer in resolved:
        if FLOW_ROLES[producer.role] != "producer":
            continue
        for consumer in resolved:
            if FLOW_ROLES[consumer.role] != "consumer" or consumer.repository == producer.repository:
                continue
            if consumer.resource != producer.resource:
                continue
            by_identity = {}
            for profile in matching_profiles(producer, consumer):
                by_identity.setdefault(producer.identity(profile), []).append(profile)
            for identity, profiles in sorted(by_identity.items()):
                result.setdefault((producer.repository, consumer.repository), []).append({
                    "resource": producer.resource, "identity": identity, "profiles": profiles,
                    "producer": producer, "consumer": consumer})
    return result


def table_shares(inventories, pairing=None, draft=None):
    """Return {(repository, repository): [match]} for tables named by two repositories in any role."""
    by_table = {}
    for item in resolve_points(inventories, pairing, draft):
        if item.resource == "table":
            by_table.setdefault(item.identity(None), []).append(item)
    result = {}
    for table, items in sorted(by_table.items()):
        repositories = sorted({item.repository for item in items})
        for first in repositories:
            for second in repositories:
                if first < second:
                    result.setdefault((first, second), []).append({
                        "resource": "table", "identity": table,
                        "points": [item for item in items if item.repository in {first, second}]})
    return result
