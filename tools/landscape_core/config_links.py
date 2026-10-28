"""Deterministic shared-resource relationships derived from configuration observations."""

import copy

from .catalog_proposals import canonicalize_catalog_proposal

RELATIONSHIP_TYPE = "application-shares-resource-with-application"
MATCHED_RESOURCES = ("jdbc", "kafka-topic", "sqs")
MISSING = "Direction of data flow and whether the shared resource is used at runtime are not established by configuration alone."


def _slug(value):
    return "".join(character if character.isalnum() else "-" for character in value.lower()).strip("-")


def _entry_for(bundles, repository, commit, observation_id):
    for bundle in bundles:
        if bundle["repository"] == repository and bundle["commit"] == commit:
            for entry in bundle["selectedEvidence"]:
                if observation_id in entry["observationIds"]:
                    return bundle, entry
    return None, None


def _line_evidence(bundle, entry, line, observation_id):
    first, _, last = entry["lines"].partition("-")
    inside = int(first) <= line <= int(last or first)
    return {"kind": "repository", "repositoryId": bundle["repository"], "commit": bundle["commit"],
            "path": entry["path"], "lines": str(line) if inside else entry["lines"],
            "observationId": observation_id}


def shared_resources(inventories):
    """Group literal resource values by (resource, value) and keep those seen in two or more repositories."""
    groups = {}
    for inventory in inventories:
        for item in inventory["observations"]:
            value = item.get("value", {})
            if item["kind"] != "config-property" or value["resource"] not in MATCHED_RESOURCES:
                continue
            if value["disposition"] != "literal" or value["value"].startswith(("localhost", "http://localhost")):
                continue
            groups.setdefault((value["resource"], value["value"]), []).append(item)
    return {key: items for key, items in groups.items()
            if len({item["repository"] for item in items}) >= 2}


def propose_shared_resource_relationships(proposal, inventories, bundles, analyzed_at):
    """Return a copy of ``proposal`` whose shared-resource relationships are rebuilt from configuration."""
    result = copy.deepcopy(proposal)
    application_by_repository = {}
    for application in result["applications"]:
        for evidence in application["assessment"]["evidence"]:
            if evidence["kind"] == "repository":
                application_by_repository.setdefault(evidence["repositoryId"], application["id"])
    result["relationships"] = [item for item in result["relationships"] if item["type"] != RELATIONSHIP_TYPE]
    pairs = {}
    for (resource, value), items in sorted(shared_resources(inventories).items()):
        for first in sorted({item["repository"] for item in items}):
            for second in sorted({item["repository"] for item in items}):
                if first >= second or first not in application_by_repository or second not in application_by_repository:
                    continue
                key = tuple(sorted((application_by_repository[first], application_by_repository[second])))
                pairs.setdefault(key, []).append(
                    (resource, value, [item for item in items if item["repository"] in {first, second}]))
    for (source, target), matches in sorted(pairs.items()):
        evidence, statements = [], []
        for resource, value, items in matches:
            statements.append("{} `{}`".format(resource, value))
            for item in items:
                bundle, entry = _entry_for(bundles, item["repository"], item["commit"], item["id"])
                if entry is not None:
                    evidence.append(_line_evidence(bundle, entry, item["value"]["line"], item["id"]))
        if not evidence:
            continue
        unique = {tuple(sorted(item.items())): item for item in evidence}
        result["relationships"].append({
            "id": "rel-{}-shares-resource-with-{}".format(_slug(source), _slug(target)),
            "type": RELATIONSHIP_TYPE,
            "source": {"entityType": "application", "entityId": source},
            "target": {"entityType": "application", "entityId": target},
            "statement": "Both applications' configuration holds the same value for: {}.".format("; ".join(statements)),
            "assessment": {"status": "inferred", "confidence": "medium", "analyzedAt": analyzed_at,
                           "evidence": list(unique.values()), "counterevidence": [],
                           "missingEvidence": [MISSING]},
        })
    return canonicalize_catalog_proposal(result)
