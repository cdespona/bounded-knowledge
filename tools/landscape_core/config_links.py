"""Deterministic application relationships derived from configuration and I/O observations."""

import copy

from .catalog_proposals import canonicalize_catalog_proposal
from .io_links import feed_matches, table_shares

SHARE_TYPE = "application-shares-resource-with-application"
FEED_TYPE = "application-feeds-application"
RELATIONSHIP_TYPE = SHARE_TYPE
MATCHED_RESOURCES = ("jdbc", "kafka-topic", "sqs")
SHARE_MISSING = "Direction of data flow and whether the shared resource is used at runtime are not established by configuration alone."
FEED_MISSING = ("Whether both deployments run against the same cluster, account, or database instance is not "
                "established by code and configuration alone.")
VERBS = {"table": ("writes", "reads"), "s3-bucket": ("writes to", "reads from")}
CONFIG_COVERED = {"kafka-topic": "kafka-topic", "sqs": "sqs-queue"}


def _slug(value):
    return "".join(character if character.isalnum() else "-" for character in value.lower()).strip("-")


def _entry_for(bundles, repository, commit, observation_id):
    for bundle in bundles:
        if bundle["repository"] == repository and bundle["commit"] == commit:
            for entry in bundle["selectedEvidence"]:
                if observation_id in entry["observationIds"]:
                    return bundle, entry
    return None, None


def _range_evidence(bundle, entry, lines, observation_id):
    first, _, last = entry["lines"].partition("-")
    low, _, high = lines.partition("-")
    inside = int(first) <= int(low) and int(high or low) <= int(last or first)
    return {"kind": "repository", "repositoryId": bundle["repository"], "commit": bundle["commit"],
            "path": entry["path"], "lines": lines if inside else entry["lines"], "observationId": observation_id}


def _evidence(bundles, item):
    """Return repository evidence for one observation, or None if it was not selected into a bundle."""
    bundle, entry = _entry_for(bundles, item["repository"], item["commit"], item["id"])
    if entry is None:
        return None
    if item["kind"] == "config-property":
        lines = str(item["value"]["line"])
    else:
        lines = item["source"]["lines"]
    return _range_evidence(bundle, entry, lines, item["id"])


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


def _profiles(profiles):
    return ", ".join(profile or "default" for profile in profiles)


def _feed_statement(match):
    verbs = VERBS.get(match["resource"], ("produces to", "consumes from"))
    return "{} {} `{}` (profiles: {})".format(verbs[0], match["resource"], match["identity"], _profiles(match["profiles"]))


def _feed_missing(matches):
    missing = [FEED_MISSING]
    notes = {item.note for match in matches for item in (match["producer"], match["consumer"]) if item.note}
    if "queue name only" in notes:
        missing.append("SQS queues are compared by queue name only; region and account are not compared.")
    if "channel name used as the topic" in notes:
        missing.append("A messaging channel without a configured topic is read as using the channel name as the topic.")
    return missing


def _unique(evidence):
    return list({tuple(sorted(item.items())): item for item in evidence if item is not None}.values())


def propose_shared_resource_relationships(proposal, inventories, bundles, analyzed_at):
    """Return a copy of ``proposal`` whose derived application relationships are rebuilt from observations."""
    result = copy.deepcopy(proposal)
    application_by_repository = {}
    for application in result["applications"]:
        for evidence in application["assessment"]["evidence"]:
            if evidence["kind"] == "repository":
                application_by_repository.setdefault(evidence["repositoryId"], application["id"])
    result["relationships"] = [item for item in result["relationships"] if item["type"] not in {SHARE_TYPE, FEED_TYPE}]

    def application_pair(first, second):
        if first in application_by_repository and second in application_by_repository:
            return application_by_repository[first], application_by_repository[second]
        return None

    feeds = {}
    for (producer, consumer), matches in sorted(feed_matches(inventories).items()):
        pair = application_pair(producer, consumer)
        if pair is not None:
            feeds[pair] = matches

    covered = {frozenset(pair): {match["resource"] for match in matches} for pair, matches in feeds.items()}
    for (source, target), matches in sorted(feeds.items()):
        evidence = []
        for match in matches:
            for item in (match["producer"], match["consumer"]):
                evidence.append(_evidence(bundles, item.point))
                evidence.extend(_evidence(bundles, config) for config in item.config_items)
        evidence = _unique(evidence)
        cited = {item["observationId"] for item in evidence}
        matches = [match for match in matches
                   if match["producer"].point["id"] in cited and match["consumer"].point["id"] in cited]
        if not matches:
            continue
        statements = [_feed_statement(match) for match in matches]
        result["relationships"].append({
            "id": "rel-{}-feeds-{}".format(_slug(source), _slug(target)),
            "type": FEED_TYPE,
            "source": {"entityType": "application", "entityId": source},
            "target": {"entityType": "application", "entityId": target},
            "statement": "The source application's code {}; the target application's code consumes from or reads the same target(s).".format(
                "; ".join(sorted(set(statements)))),
            "assessment": {"status": "inferred", "confidence": "medium", "analyzedAt": analyzed_at,
                           "evidence": evidence, "counterevidence": [],
                           "missingEvidence": _feed_missing(matches)},
        })

    shares = {}
    for (resource, value), items in sorted(shared_resources(inventories).items()):
        repositories = sorted({item["repository"] for item in items})
        for first in repositories:
            for second in repositories:
                pair = application_pair(first, second) if first < second else None
                if pair is None or CONFIG_COVERED.get(resource) in covered.get(frozenset(pair), set()):
                    continue
                key = tuple(sorted(pair))
                shares.setdefault(key, []).append((
                    "{} `{}`".format(resource, value),
                    [_evidence(bundles, item) for item in items if item["repository"] in {first, second}]))
    fed_tables = {(pair, match["identity"]) for pair, matches in feeds.items()
                  for match in matches if match["resource"] == "table"}
    for (first, second), matches in sorted(table_shares(inventories).items()):
        pair = application_pair(first, second)
        if pair is None:
            continue
        for match in matches:
            if (pair, match["identity"]) in fed_tables or (pair[::-1], match["identity"]) in fed_tables:
                continue
            shares.setdefault(tuple(sorted(pair)), []).append((
                "table `{}`".format(match["identity"]), [_evidence(bundles, item.point) for item in match["points"]]))
    for (source, target), entries in sorted(shares.items()):
        evidence = _unique([item for _, items in entries for item in items])
        if not evidence:
            continue
        result["relationships"].append({
            "id": "rel-{}-shares-resource-with-{}".format(_slug(source), _slug(target)),
            "type": SHARE_TYPE,
            "source": {"entityType": "application", "entityId": source},
            "target": {"entityType": "application", "entityId": target},
            "statement": "Both applications name the same value for: {}.".format(
                "; ".join(sorted({statement for statement, _ in entries}))),
            "assessment": {"status": "inferred", "confidence": "medium", "analyzedAt": analyzed_at,
                           "evidence": evidence, "counterevidence": [],
                           "missingEvidence": [SHARE_MISSING]},
        })
    return canonicalize_catalog_proposal(result)
