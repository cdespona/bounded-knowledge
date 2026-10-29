"""Bindings between source selections and catalog applications, proposed by folder convention."""

import copy
from pathlib import PurePosixPath


def application_repositories(catalog):
    """Map each application id to the repositories its repository evidence names."""
    result = {}
    for application in catalog["applications"]:
        for evidence in application["assessment"]["evidence"]:
            if evidence["kind"] == "repository":
                result.setdefault(application["id"], set()).add(evidence["repositoryId"])
    return result


def _repository_applications(catalog):
    result = {}
    for application, repositories in sorted(application_repositories(catalog).items()):
        for repository in repositories:
            result.setdefault(repository, application)
    return result


def infer_bindings(topology, catalog):
    """Return a topology with an inferred binding for every unbound selection that a convention maps."""
    result = copy.deepcopy(topology)
    bound = {item["sourceSelectionId"] for item in result["bindings"]}
    by_repository = _repository_applications(catalog)
    for selection in result["sourceSelections"]:
        if selection["id"] in bound:
            continue
        if selection["kind"] == "application":
            key, detail = selection["repositoryId"], "The application evidence of the catalog names repository `{}`."
        else:
            key, detail = PurePosixPath(selection["subpath"]).name, "The folder `{}` is named like the repository of the application."
        application = by_repository.get(key)
        if application is not None:
            result["bindings"].append({
                "sourceSelectionId": selection["id"], "target": {"type": "application", "id": application},
                "status": "inferred", "provenance": {"method": "folder-convention", "detail": detail.format(key)}})
    result["bindings"].sort(key=lambda item: (item["sourceSelectionId"], item["target"]["type"], item["target"]["id"]))
    return result


def selection_pairs(topology, proposal):
    """Map each selection id to (repositories of its bound applications, whether a human declared the binding)."""
    repositories = application_repositories(proposal)
    owners = {}
    for relationship in proposal["relationships"]:
        if relationship["type"] == "application-comprises-deployable":
            owners[relationship["target"]["entityId"]] = relationship["source"]["entityId"]
    result = {}
    for binding in topology["bindings"]:
        target = binding["target"]
        application = target["id"] if target["type"] == "application" else owners.get(target["id"])
        found = repositories.get(application)
        if found:
            previous = result.get(binding["sourceSelectionId"], (set(), True))
            result[binding["sourceSelectionId"]] = (previous[0] | found, previous[1] and binding["status"] == "declared")
    return result
