"""Deterministic ids for model-proposed catalogs, so reruns name the same thing the same way."""

import re

from .contracts import CATALOG_COLLECTION_TYPES, CATALOG_RELATIONSHIPS

_RECONCILED = ("boundedContexts", "useCases", "strategicCapabilities")


def slug(value):
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "-", str(value))
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _repository(item):
    repositories = {entry["repositoryId"] for entry in item.get("assessment", {}).get("evidence", [])
                    if isinstance(entry, dict) and entry.get("kind") == "repository" and "repositoryId" in entry}
    return next(iter(repositories)) if len(repositories) == 1 else None


def _predicate(relationship_type):
    source, target = CATALOG_RELATIONSHIPS[relationship_type]
    return relationship_type[len(source) + 1:-(len(target) + 1)]


def relationship_id(relationship):
    return "{}-{}-{}".format(relationship["source"]["entityId"], _predicate(relationship["type"]),
                             relationship["target"]["entityId"])


def convention_errors(proposal):
    """Name every application, deployable and term whose id departs from the convention."""
    errors = []
    applications = {item["id"]: _repository(item) for item in proposal.get("applications", [])}
    for item in proposal.get("applications", []):
        repository = _repository(item)
        if repository and item["id"] != repository + "-app":
            errors.append("applications[{}] must be named {}-app after its single repository".format(item["id"], repository))
    for link in proposal.get("relationships", []):
        if link.get("type") == "application-comprises-deployable":
            repository = applications.get(link["source"]["entityId"])
            if repository and link["target"]["entityId"] != repository + "-deployable":
                errors.append("deployables[{}] must be named {}-deployable after its application's repository".format(
                    link["target"]["entityId"], repository))
    for item in proposal.get("terms", []):
        if item["id"] != slug(item.get("name", "")):
            errors.append("terms[{}] must be named {} after its name".format(item["id"], slug(item.get("name", ""))))
    return errors


def normalize_ids(proposal, current=None):
    """Rename entities and relationships by convention; return the proposal and a sorted list of (kind, old, new).

    Applications, deployables and terms follow fixed patterns. Bounded contexts, use cases and capabilities
    are concepts, so they adopt the id of a same-named entity in ``current``. Relationships adopt the id of the
    current relationship with the same type, source and target, else follow the pattern.
    """
    renames = {}
    applications = {item["id"]: _repository(item) for item in proposal.get("applications", [])}
    for collection, entities in [(c, proposal.get(c, [])) for c in CATALOG_COLLECTION_TYPES]:
        existing = {slug(item.get("name", "")): item["id"] for item in (current or {}).get(collection, [])}
        for item in entities:
            new = item["id"]
            if collection == "applications" and applications[item["id"]]:
                new = applications[item["id"]] + "-app"
            elif collection == "terms":
                new = slug(item.get("name", "")) or new
            elif collection in _RECONCILED:
                new = existing.get(slug(item.get("name", "")), new)
            if new != item["id"]:
                renames[(collection, item["id"])] = new
    for link in proposal.get("relationships", []):
        if link.get("type") == "application-comprises-deployable":
            repository = applications.get(link["source"]["entityId"])
            if repository and link["target"]["entityId"] != repository + "-deployable":
                renames[("deployables", link["target"]["entityId"])] = repository + "-deployable"

    def renamed(entity_type, entity_id):
        collection = next(c for c, t in CATALOG_COLLECTION_TYPES.items() if t == entity_type)
        return renames.get((collection, entity_id), entity_id)

    for collection in CATALOG_COLLECTION_TYPES:
        for item in proposal.get(collection, []):
            item["id"] = renames.get((collection, item["id"]), item["id"])
        ids = [item["id"] for item in proposal.get(collection, [])]
        if len(ids) != len(set(ids)):
            raise ValueError("id normalization would give two {} the same id".format(collection))
    known = {}
    for item in (current or {}).get("relationships", []):
        known[(item["type"], item["source"]["entityId"], item["target"]["entityId"])] = item["id"]
    for link in proposal.get("relationships", []):
        for end in ("source", "target"):
            link[end]["entityId"] = renamed(link[end]["entityType"], link[end]["entityId"])
        if link["type"] in CATALOG_RELATIONSHIPS:
            identity = (link["type"], link["source"]["entityId"], link["target"]["entityId"])
            new = known.get(identity, relationship_id(link))
            if new != link["id"]:
                renames[("relationships", link["id"])] = new
                link["id"] = new
    ids = [(link["type"], link["id"]) for link in proposal.get("relationships", [])]
    if len(ids) != len(set(ids)):
        raise ValueError("id normalization would give two relationships the same id")
    return proposal, sorted((collection, old, new) for (collection, old), new in renames.items())
