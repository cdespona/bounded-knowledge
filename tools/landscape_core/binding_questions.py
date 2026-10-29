"""Deterministic questions for targets that code, configuration, and manifests leave unresolved."""

import re

from .contracts import artifact_id, validate_questionnaire_draft
from .io_links import unresolved_points

NOUNS = {"kafka": "Kafka topic", "sqs": "SQS queue (name or URL)", "s3": "S3 bucket name", "amqp": "queue or exchange"}
VERBS = {"producer": "produce to", "consumer": "consume from"}
SOURCES = {"identifier": "a variable that no configuration read in this repository assigns",
           "expression": "a computed expression", "interpolated-string": "an interpolated string"}
MAX_ID = 100


def _slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "target"


def _bundle_for(bundles, observation):
    for bundle in bundles:
        if bundle["repository"] == observation["repository"] and bundle["commit"] == observation["commit"]:
            for entry in bundle["selectedEvidence"]:
                if observation["id"] in entry["observationIds"]:
                    return bundle
    return None


def _wrapped(observation, points):
    """An unresolved parameter inside a method whose callers were resolved asks nothing new."""
    value = observation["value"]
    return value["binding"]["form"] == "unresolved" and any(
        other["repository"] == observation["repository"] and other["value"]["binding"]["form"] != "unresolved"
        and other["value"]["construct"].startswith(value["construct"] + " via ") for other in points)


def _why(observation, manifest_notes):
    binding = observation["value"]["binding"]
    if binding["form"] == "config-key":
        text = "The code reads this target from the configuration key `{}`, which has no literal value in the repository " \
               "properties or in the paired Kubernetes manifests.".format(binding["reference"])
        extra = manifest_notes.get(binding["reference"])
        return text + (" The manifests pass it as {}.".format(extra) if extra else "")
    return ("The code takes this target from `{}`, {}, so static reading cannot resolve it to a configured value."
            .format(binding["reference"], SOURCES.get(binding["via"], "a value that is not a literal")))


def _manifest_notes(inventories):
    notes = {}
    for inventory in inventories:
        for item in inventory["observations"]:
            if item["kind"] == "env-variable" and item["value"]["disposition"] in {"reference", "withheld"}:
                value = item["value"]
                notes[value["name"]] = "a {}".format(value["reference"]) if value["reference"] else "a withheld value"
    return notes


def generate_binding_questions(inventories, bundles, respondent, pairing=None, draft=None):
    """Return (draft, skipped) with one unanswered question per distinct unresolved target of a repository.

    ``skipped`` counts unresolved observations that no evidence bundle selected, so no scoped link exists.
    """
    points = unresolved_points(inventories, pairing, draft)
    all_unresolved = list(points)
    points = [item for item in points if not _wrapped(item, all_unresolved)]
    notes = _manifest_notes(inventories)
    groups, skipped = {}, 0
    for item in points:
        bundle = _bundle_for(bundles, item)
        if bundle is None:
            skipped += 1
            continue
        value = item["value"]
        key = (item["repository"], value["technology"], value["role"], value["binding"]["reference"])
        groups.setdefault(key, []).append((item, bundle))
    scope, questions, used = {}, [], set()
    for (repository, technology, role, reference), found in sorted(groups.items()):
        first = found[0][0]
        identifier = "binding-{}-{}-{}-{}".format(repository, technology, role, _slug(reference))[:MAX_ID].rstrip("-")
        while identifier in used:
            identifier += "-x"
        used.add(identifier)
        links = []
        for item, bundle in found:
            scope[(bundle["repository"], bundle["commit"], bundle["id"])] = True
            links.append({"type": "observation", "repository": bundle["repository"], "commit": bundle["commit"],
                          "evidenceBundleId": bundle["id"], "observationId": item["id"]})
        places = ", ".join("`{}:{}`".format(item["source"]["path"], item["source"]["lines"]) for item, _ in found[:3])
        questions.append({
            "id": identifier, "reviewType": "human-only", "expects": "target-values", "answerStatus": "unanswered",
            "wording": "For `{repository}`, which {noun} does the code {verb} through `{reference}` (read at {places}) in each "
                       "environment? Give one value per environment, such as dev and pro.".format(
                           noun=NOUNS[technology], repository=repository, verb=VERBS[role], reference=reference, places=places),
            "links": sorted(links, key=lambda link: link["observationId"]),
            "whyCodeCannotSettle": _why(first, notes)})
    if not questions:
        return None, skipped
    result = {"schemaVersion": 1, "respondent": respondent,
              "scope": [{"repository": r, "commit": c, "evidenceBundleId": b} for r, c, b in sorted(scope)],
              "questions": sorted(questions, key=lambda item: item["id"])}
    result["id"] = artifact_id(result)
    errors = validate_questionnaire_draft(result)
    if errors:
        raise ValueError("generated questionnaire is invalid: " + "; ".join(errors[:5]))
    return result, skipped
