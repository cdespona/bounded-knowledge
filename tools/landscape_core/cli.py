"""Command-line interface for deterministic landscape tooling."""

import argparse
import json
from pathlib import Path
import re
import sys

from .contracts import validate_source_registry
from .discovery import discover, preflight
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
        prog="landscape", description="Deterministic Mercurio landscape discovery"
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
    except (OSError, ValueError, RepositoryError, json.JSONDecodeError) as error:
        sys.stderr.write("error: {}\n".format(error))
        return 1
    return 1
