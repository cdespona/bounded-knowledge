"""Resolve I/O points against configuration and pair producers with consumers across repositories."""

LOCAL_PROFILES = {"local", "test"}
TECHNOLOGY_RESOURCES = {"kafka": "kafka-topic", "sqs": "sqs-queue", "s3": "s3-bucket", "amqp": "amqp-destination"}
CONNECTOR_RESOURCES = (("kafka", "kafka-topic", "topic"), ("amqp", "amqp-destination", "address"),
                       ("rabbit", "amqp-destination", "address"))
DIRECTION = {"producer": "outgoing", "consumer": "incoming"}
FLOW_ROLES = {"producer": "producer", "consumer": "consumer", "writer": "producer", "reader": "consumer"}


class Resolved:
    """An I/O point whose target was resolved to comparable identities per configuration profile."""

    def __init__(self, point, resource, by_profile, config_items, note=None):
        self.point = point
        self.resource = resource
        self.by_profile = by_profile
        self.config_items = config_items
        self.note = note

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


def _values(index, key):
    """Return ({profile: value}, items) for a key; later files in sorted order win within one profile."""
    by_profile, items = {}, []
    for item in sorted(index.get(key, []), key=lambda entry: (entry["source"]["path"], entry["value"]["line"])):
        by_profile[item["value"]["profile"]] = item["value"]["value"]
        items.append(item)
    return by_profile, items


def _identity(resource, value):
    value = value.strip()
    if resource == "sqs-queue":
        return value.rstrip("/").rsplit("/", 1)[-1]
    if resource == "table":
        return value.strip('"`').upper()
    return value


def _resolve(point, index):
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
        values, items = _values(index, binding["reference"])
        if values:
            return Resolved(point, resource, {profile: _identity(resource, text) for profile, text in values.items()},
                            items, "queue name only" if resource == "sqs-queue" else None)
        return None
    if form == "channel" and technology == "messaging":
        prefix = "mp.messaging.{}.{}.".format(DIRECTION[value["role"]], binding["reference"])
        connectors, connector_items = _values(index, prefix + "connector")
        for needle, resource, key in CONNECTOR_RESOURCES:
            if any(needle in text for text in connectors.values()):
                values, items = _values(index, prefix + key)
                if values:
                    return Resolved(point, resource, {profile: _identity(resource, text) for profile, text in values.items()},
                                    connector_items + items)
                excluded = {prefix + "topics", prefix + "topic-pattern", prefix + "pattern"}
                if resource == "kafka-topic" and not any(name in index for name in excluded):
                    return Resolved(point, resource, {None: binding["reference"]}, connector_items,
                                    "channel name used as the topic")
    return None


def resolve_points(inventories):
    resolved = []
    for inventory in inventories:
        index = _configuration(inventory)
        for item in inventory["observations"]:
            if item["kind"] == "io-point":
                result = _resolve(item, index)
                if result is not None:
                    resolved.append(result)
    return resolved


def matching_profiles(first, second):
    """Return the shared-environment profiles in which two resolved points name the same target."""
    profiles = {profile for profile in set(first.by_profile) | set(second.by_profile) if profile not in LOCAL_PROFILES}
    matched = []
    for profile in sorted(profiles, key=lambda name: (name is not None, name or "")):
        left, right = first.identity(profile), second.identity(profile)
        if left is not None and left == right:
            matched.append(profile)
    return matched


def feed_matches(inventories):
    """Return {(producer repository, consumer repository): [match]} for producers paired with consumers."""
    resolved = [item for item in resolve_points(inventories) if item.role in {"producer", "consumer", "writer", "reader"}]
    result = {}
    for producer in resolved:
        if FLOW_ROLES[producer.role] != "producer":
            continue
        for consumer in resolved:
            if FLOW_ROLES[consumer.role] != "consumer" or consumer.repository == producer.repository:
                continue
            if consumer.resource != producer.resource:
                continue
            profiles = matching_profiles(producer, consumer)
            if profiles:
                identity = producer.identity(profiles[0])
                result.setdefault((producer.repository, consumer.repository), []).append({
                    "resource": producer.resource, "identity": identity, "profiles": profiles,
                    "producer": producer, "consumer": consumer})
    return result


def table_shares(inventories):
    """Return {(repository, repository): [match]} for tables named by two repositories in any role."""
    by_table = {}
    for item in resolve_points(inventories):
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
