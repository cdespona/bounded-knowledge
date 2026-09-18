"""Command-line interface for deterministic landscape tooling."""

import argparse
import json
from pathlib import Path
import re
import sys

from .contracts import (
    validate_landscape_catalog,
    validate_source_registry,
    validate_source_topology,
)
from .candidates import extract_candidate_response, validate_candidate
from .discovery import discover, preflight
from .evidence import select_evidence
from .git_repository import RepositoryError, commit_sha, require_repository_root
from .sources import resolve_source
from .validation import validate_inventory


def _repository_id(value):
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", value):
        raise argparse.ArgumentTypeError("repository identifiers must use lowercase kebab-case")
    return value


def _write_json(document, output=None):
    content = json.dumps(document, indent=2, sort_keys=True) + "\n"
    if output:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    else:
        sys.stdout.write(content)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="landscape", description="Deterministic landscape discovery"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight_parser = subparsers.add_parser("preflight", help="check repository safety")
    preflight_parser.add_argument("source")

    discover_parser = subparsers.add_parser("discover", help="produce a stable inventory")
    discover_parser.add_argument("source")
    discover_parser.add_argument("--repository", required=True, type=_repository_id)
    discover_parser.add_argument("--output")

    validate_parser = subparsers.add_parser("validate", help="validate an inventory")
    validate_parser.add_argument("inventory")
    validate_parser.add_argument("--source")

    status_parser = subparsers.add_parser("status", help="compare repository HEAD to inventory")
    status_parser.add_argument("source")
    status_parser.add_argument("--inventory", required=True)

    sources_parser = subparsers.add_parser(
        "sources", help="validate and resolve registered source repositories"
    )
    sources_subparsers = sources_parser.add_subparsers(
        dest="sources_command", required=True
    )
    sources_validate_parser = sources_subparsers.add_parser(
        "validate", help="validate a source registry"
    )
    sources_validate_parser.add_argument("registry")
    sources_resolve_parser = sources_subparsers.add_parser(
        "resolve", help="resolve an approved source repository"
    )
    sources_resolve_parser.add_argument("repository", type=_repository_id)
    sources_resolve_parser.add_argument("--registry", required=True)

    evidence_parser = subparsers.add_parser(
        "evidence", help="select bounded evidence from an inventory"
    )
    evidence_subparsers = evidence_parser.add_subparsers(
        dest="evidence_command", required=True
    )
    evidence_select_parser = evidence_subparsers.add_parser(
        "select", help="create a deterministic evidence bundle"
    )
    evidence_select_parser.add_argument("inventory")
    evidence_select_parser.add_argument("--source", required=True)
    evidence_select_parser.add_argument("--output", required=True)

    candidate_parser = subparsers.add_parser(
        "candidate", help="validate model-produced candidate profiles"
    )
    candidate_subparsers = candidate_parser.add_subparsers(
        dest="candidate_command", required=True
    )
    candidate_validate_parser = candidate_subparsers.add_parser(
        "validate", help="validate a candidate against an evidence bundle"
    )
    candidate_validate_parser.add_argument("candidate")
    candidate_validate_parser.add_argument("--evidence", required=True)
    candidate_extract_parser = candidate_subparsers.add_parser(
        "extract", help="extract one JSON candidate from a captured model response"
    )
    candidate_extract_parser.add_argument("response")
    candidate_extract_parser.add_argument("--output", required=True)

    catalog_parser = subparsers.add_parser(
        "catalog", help="validate canonical landscape knowledge"
    )
    catalog_subparsers = catalog_parser.add_subparsers(
        dest="catalog_command", required=True
    )
    catalog_validate_parser = catalog_subparsers.add_parser(
        "validate", help="validate a landscape catalog"
    )
    catalog_validate_parser.add_argument("catalog")

    topology_parser = subparsers.add_parser(
        "topology", help="validate logical source selections and bindings"
    )
    topology_subparsers = topology_parser.add_subparsers(
        dest="topology_command", required=True
    )
    topology_validate_parser = topology_subparsers.add_parser(
        "validate", help="validate source topology against sources and catalog"
    )
    topology_validate_parser.add_argument("topology")
    topology_validate_parser.add_argument("--sources", required=True)
    topology_validate_parser.add_argument("--catalog", required=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preflight":
            result = preflight(Path(args.source))
            _write_json(result)
            return 0 if result["clean"] else 2

        if args.command == "discover":
            _write_json(
                discover(Path(args.source), args.repository),
                output=args.output,
            )
            return 0

        if args.command == "validate":
            document = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
            errors = validate_inventory(document, source=args.source)
            _write_json({"valid": not errors, "errors": errors})
            return 0 if not errors else 1

        if args.command == "status":
            document = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
            root = require_repository_root(Path(args.source))
            current = commit_sha(root)
            _write_json(
                {
                    "repository": document.get("repository"),
                    "inventoryCommit": document.get("commit"),
                    "currentCommit": current,
                    "changed": current != document.get("commit"),
                }
            )
            return 0

        if args.command == "sources" and args.sources_command == "validate":
            document = json.loads(Path(args.registry).read_text(encoding="utf-8"))
            errors = validate_source_registry(document)
            _write_json({"valid": not errors, "errors": errors})
            return 0 if not errors else 1

        if args.command == "sources" and args.sources_command == "resolve":
            result = resolve_source(args.registry, args.repository)
            _write_json(result)
            return 0 if result["copilotAccessApproved"] else 2

        if args.command == "evidence" and args.evidence_command == "select":
            inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
            _write_json(select_evidence(inventory, args.source), output=args.output)
            return 0

        if args.command == "candidate" and args.candidate_command == "validate":
            candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
            evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
            errors = validate_candidate(candidate, evidence)
            _write_json({"valid": not errors, "errors": errors})
            return 0 if not errors else 1

        if args.command == "candidate" and args.candidate_command == "extract":
            response = Path(args.response).read_text(encoding="utf-8")
            _write_json(extract_candidate_response(response), output=args.output)
            return 0

        if args.command == "catalog" and args.catalog_command == "validate":
            catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
            errors = validate_landscape_catalog(catalog)
            _write_json({"valid": not errors, "errors": errors})
            return 0 if not errors else 1

        if args.command == "topology" and args.topology_command == "validate":
            topology = json.loads(Path(args.topology).read_text(encoding="utf-8"))
            sources = json.loads(Path(args.sources).read_text(encoding="utf-8"))
            catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
            errors = validate_source_topology(topology, sources, catalog)
            _write_json({"valid": not errors, "errors": errors})
            return 0 if not errors else 1
    except (OSError, ValueError, RepositoryError, json.JSONDecodeError) as error:
        sys.stderr.write("error: {}\n".format(error))
        return 1
    return 1
