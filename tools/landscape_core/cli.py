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
from .config_links import propose_shared_resource_relationships
from .promotion import build_approval, promote, serialize
from .catalog_proposals import extract_catalog_proposal, render_catalog_review, validate_catalog_proposal
from .questionnaires import answers_template, answers_template_markdown, parse_answers_markdown, assemble_questionnaire, plan_extracted_files, record_answers, consolidate_questionnaire, extract_questionnaire_response, render_questionnaire
from .contracts import validate_questionnaire_draft
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
    discover_parser.add_argument("--topology")
    discover_parser.add_argument("--selection", type=_repository_id)
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
    evidence_select_parser.add_argument("--purpose", choices=("general", "semantic"), default="general")


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

    questionnaire_parser = subparsers.add_parser("questionnaire", help="validate and render draft Q&A")
    questionnaire_subparsers = questionnaire_parser.add_subparsers(dest="questionnaire_command", required=True)
    questionnaire_validate_parser = questionnaire_subparsers.add_parser("validate", help="validate a draft Q&A record")
    questionnaire_validate_parser.add_argument("draft")
    questionnaire_extract_parser = questionnaire_subparsers.add_parser("extract", help="extract one draft from a model response")
    questionnaire_extract_parser.add_argument("response")
    questionnaire_extract_parser.add_argument("--output", required=True)
    questionnaire_assemble_parser = questionnaire_subparsers.add_parser("assemble", help="merge bounded batch responses into one draft")
    questionnaire_assemble_parser.add_argument("responses", nargs="+")
    questionnaire_assemble_parser.add_argument("--output", required=True)
    questionnaire_assemble_parser.add_argument("--extracted-dir", help="also write each validated batch as <input stem>.extracted.json")
    questionnaire_consolidate_parser = questionnaire_subparsers.add_parser("consolidate", help="accept a lossless model merge of overlapping questions")
    questionnaire_consolidate_parser.add_argument("draft")
    questionnaire_consolidate_parser.add_argument("response")
    questionnaire_consolidate_parser.add_argument("--output", required=True)
    questionnaire_template_parser = questionnaire_subparsers.add_parser("answers-template", help="write an empty answer sheet for the human-only questions")
    questionnaire_template_parser.add_argument("draft")
    questionnaire_template_parser.add_argument("--output", required=True)
    questionnaire_template_parser.add_argument("--force", action="store_true", help="overwrite an existing sheet (discards its edits)")
    questionnaire_template_parser.add_argument("--review-type", choices=["human-only", "code-answerable"], default="human-only")
    questionnaire_record_parser = questionnaire_subparsers.add_parser("record", help="record an attributed answer sheet into a draft")
    questionnaire_record_parser.add_argument("draft")
    questionnaire_record_parser.add_argument("answers")
    questionnaire_record_parser.add_argument("--output", required=True)
    questionnaire_record_parser.add_argument("--recorded-at", help="override the recording time (ISO 8601 with timezone)")
    questionnaire_render_parser = questionnaire_subparsers.add_parser("render", help="render a validated draft as Markdown")
    questionnaire_render_parser.add_argument("draft")
    questionnaire_render_parser.add_argument("--output", required=True)

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
    catalog_extract_parser = catalog_subparsers.add_parser(
        "extract", help="extract one proposed catalog from a model response"
    )
    catalog_extract_parser.add_argument("response")
    catalog_extract_parser.add_argument("--output", required=True)
    catalog_propose_parser = catalog_subparsers.add_parser(
        "propose-validate", help="validate a proposed catalog against answers and evidence"
    )
    catalog_propose_parser.add_argument("proposal")
    catalog_propose_parser.add_argument("--draft", required=True)
    catalog_propose_parser.add_argument("--evidence", required=True, nargs="+")
    catalog_link_parser = catalog_subparsers.add_parser(
        "link-config", help="add shared-resource relationships derived from configuration observations"
    )
    catalog_link_parser.add_argument("proposal")
    catalog_link_parser.add_argument("--inventory", required=True, nargs="+")
    catalog_link_parser.add_argument("--evidence", required=True, nargs="+")
    catalog_link_parser.add_argument("--analyzed-at", required=True)
    catalog_link_parser.add_argument("--output", required=True)
    catalog_review_parser = catalog_subparsers.add_parser(
        "review", help="render a Markdown review of a proposed catalog"
    )
    catalog_review_parser.add_argument("proposal")
    catalog_review_parser.add_argument("--current")
    catalog_review_parser.add_argument("--draft")
    catalog_review_parser.add_argument("--output", required=True)

    catalog_approve_parser = catalog_subparsers.add_parser(
        "approve", help="record a human approval bound to the digest of a proposal review"
    )
    catalog_approve_parser.add_argument("proposal")
    catalog_approve_parser.add_argument("--draft", required=True)
    catalog_approve_parser.add_argument("--current")
    catalog_approve_parser.add_argument("--reviewed", required=True, help="digest printed at the end of the review")
    catalog_approve_parser.add_argument("--by", required=True)
    catalog_approve_parser.add_argument("--at", required=True)
    catalog_approve_parser.add_argument("--output", required=True)
    catalog_promote_parser = catalog_subparsers.add_parser(
        "promote", help="write an approved proposal into the canonical catalog"
    )
    catalog_promote_parser.add_argument("proposal")
    catalog_promote_parser.add_argument("--approval", required=True)
    catalog_promote_parser.add_argument("--draft", required=True)
    catalog_promote_parser.add_argument("--evidence", required=True, nargs="+")
    catalog_promote_parser.add_argument("--catalog", required=True)
    catalog_promote_parser.add_argument("--promotions", required=True)
    catalog_promote_parser.add_argument("--dry-run", action="store_true")

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
            topology = None
            if args.topology is not None:
                topology = json.loads(Path(args.topology).read_text(encoding="utf-8"))
            _write_json(
                discover(
                    Path(args.source), args.repository,
                    topology=topology, selection=args.selection,
                ),
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
            _write_json(select_evidence(inventory, args.source, purpose=args.purpose), output=args.output)
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

        if args.command == "questionnaire" and args.questionnaire_command == "validate":
            errors = validate_questionnaire_draft(json.loads(Path(args.draft).read_text(encoding="utf-8")))
            _write_json({"valid": not errors, "errors": errors})
            return 0 if not errors else 1
        if args.command == "questionnaire" and args.questionnaire_command == "extract":
            _write_json(extract_questionnaire_response(Path(args.response).read_text(encoding="utf-8")), output=args.output)
            return 0
        if args.command == "questionnaire" and args.questionnaire_command == "assemble":
            responses = [(str(path), Path(path).read_text(encoding="utf-8")) for path in args.responses]
            if not args.extracted_dir:
                _write_json(assemble_questionnaire(responses), output=args.output)
                return 0
            draft, batches = assemble_questionnaire(responses, return_batches=True)
            planned = plan_extracted_files(batches, args.extracted_dir)
            if Path(args.output).resolve() in [path.resolve() for path, _content in planned]:
                raise ValueError("--output collides with an extracted batch file")
            _write_json(draft, output=args.output)
            for path, content in planned:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            return 0
        if args.command == "questionnaire" and args.questionnaire_command == "consolidate":
            original = json.loads(Path(args.draft).read_text(encoding="utf-8"))
            _write_json(consolidate_questionnaire(original, Path(args.response).read_text(encoding="utf-8")), output=args.output)
            return 0
        if args.command == "questionnaire" and args.questionnaire_command == "answers-template":
            if Path(args.output).exists() and not args.force:
                raise ValueError("{} exists; refusing to overwrite it (use --force to discard its edits)".format(args.output))
            sheet = answers_template(json.loads(Path(args.draft).read_text(encoding="utf-8")), args.review_type)
            if Path(args.output).suffix == ".md":
                Path(args.output).parent.mkdir(parents=True, exist_ok=True)
                Path(args.output).write_text(answers_template_markdown(sheet), encoding="utf-8")
            else:
                _write_json(sheet, output=args.output)
            return 0
        if args.command == "questionnaire" and args.questionnaire_command == "record":
            draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
            raw = Path(args.answers).read_text(encoding="utf-8")
            sheet = parse_answers_markdown(raw) if Path(args.answers).suffix == ".md" else json.loads(raw)
            _write_json(record_answers(draft, sheet, args.recorded_at), output=args.output)
            return 0
        if args.command == "questionnaire" and args.questionnaire_command == "render":
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(render_questionnaire(json.loads(Path(args.draft).read_text(encoding="utf-8"))), encoding="utf-8")
            return 0

        if args.command == "catalog" and args.catalog_command == "validate":
            catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
            errors = validate_landscape_catalog(catalog)
            _write_json({"valid": not errors, "errors": errors})
            return 0 if not errors else 1

        if args.command == "catalog" and args.catalog_command == "extract":
            _write_json(extract_catalog_proposal(Path(args.response).read_text(encoding="utf-8")), output=args.output)
            return 0
        if args.command == "catalog" and args.catalog_command == "propose-validate":
            proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
            draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
            bundles = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.evidence]
            errors = validate_catalog_proposal(proposal, draft, bundles)
            _write_json({"valid": not errors, "errors": errors})
            return 0 if not errors else 1
        if args.command == "catalog" and args.catalog_command == "link-config":
            proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
            inventories = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.inventory]
            bundles = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.evidence]
            _write_json(propose_shared_resource_relationships(proposal, inventories, bundles, args.analyzed_at),
                        output=args.output)
            return 0
        if args.command == "catalog" and args.catalog_command == "review":
            proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
            current = json.loads(Path(args.current).read_text(encoding="utf-8")) if args.current else None
            errors = validate_landscape_catalog(proposal) + (validate_landscape_catalog(current) if current else [])
            if errors:
                raise ValueError("; ".join(errors))
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            draft = json.loads(Path(args.draft).read_text(encoding="utf-8")) if args.draft else None
            Path(args.output).write_text(render_catalog_review(proposal, current, draft), encoding="utf-8")
            return 0

        if args.command == "catalog" and args.catalog_command == "approve":
            proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
            draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
            current = json.loads(Path(args.current).read_text(encoding="utf-8")) if args.current and Path(args.current).exists() else None
            approval = build_approval(proposal, current, draft, args.reviewed, args.by, args.at)
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(serialize(approval), encoding="utf-8")
            return 0
        if args.command == "catalog" and args.catalog_command == "promote":
            proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
            draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
            approval = json.loads(Path(args.approval).read_text(encoding="utf-8"))
            bundles = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.evidence]
            _write_json(promote(proposal, draft, bundles, approval, Path(args.catalog), Path(args.promotions), args.dry_run))
            return 0

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
