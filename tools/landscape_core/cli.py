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
from .topology_bindings import infer_bindings, selection_pairs
from .binding_questions import generate_binding_questions
from .promotion import build_approval, promote, serialize
from .catalog_ids import convention_errors
from .stages import CAP_EXIT_CODE, STAGE_AGENTS, check_stage, prepare_stage, run_stage, usage_totals
from . import runs
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
    questionnaire_bindings_parser = questionnaire_subparsers.add_parser(
        "bindings", help="generate questions for targets that code and configuration leave unresolved"
    )
    questionnaire_bindings_parser.add_argument("--inventory", required=True, nargs="+")
    questionnaire_bindings_parser.add_argument("--evidence", required=True, nargs="+")
    questionnaire_bindings_parser.add_argument("--topology")
    questionnaire_bindings_parser.add_argument("--proposal", help="catalog proposal used to pair manifests through bindings")
    questionnaire_bindings_parser.add_argument("--respondent-id", required=True)
    questionnaire_bindings_parser.add_argument("--respondent-role", required=True)
    questionnaire_bindings_parser.add_argument("--output", required=True)

    stage_parser = subparsers.add_parser("stage", help="prepare, run and check a model-backed stage")
    stage_subparsers = stage_parser.add_subparsers(dest="stage_command", required=True)
    stage_prepare_parser = stage_subparsers.add_parser("prepare", help="stage the inputs and prompt of one stage")
    stage_prepare_parser.add_argument("stage", choices=sorted(STAGE_AGENTS))
    stage_prepare_parser.add_argument("--output-dir")
    stage_prepare_parser.add_argument("--evidence", nargs="+")
    _run_options(stage_prepare_parser)
    stage_prepare_parser.add_argument("--candidate", nargs="+", default=[])
    stage_prepare_parser.add_argument("--answers")
    stage_prepare_parser.add_argument("--audit", help="the reviser's audit.json")
    stage_prepare_parser.add_argument("--analyzed-at")
    stage_prepare_parser.add_argument("--respondent-id", default="technical-lead")
    stage_prepare_parser.add_argument("--respondent-role", default="technical lead")
    stage_prepare_parser.add_argument("--current", help="with --run: the catalog synthesis adopts ids from")
    stage_run_parser = stage_subparsers.add_parser("run", help="run the Copilot agent of a prepared stage once")
    stage_run_parser.add_argument("stage_dir", nargs="?")
    stage_run_parser.add_argument("--output")
    stage_run_parser.add_argument("--stage", choices=sorted(STAGE_AGENTS), help="with --run: which stage folder to run")
    _run_options(stage_run_parser)
    stage_run_parser.add_argument("--timeout", type=int, default=290)
    stage_run_parser.add_argument("--max-ai-credits", type=int, help="override the stage budget (minimum 30)")
    stage_run_parser.add_argument("--model", help="override the stage model")
    stage_usage_parser = stage_subparsers.add_parser("usage", help="sum the recorded usage of stage directories")
    stage_usage_parser.add_argument("stage_dirs", nargs="+")
    stage_check_parser = stage_subparsers.add_parser("check", help="extract and validate a captured stage response")
    stage_check_parser.add_argument("stage", choices=sorted(STAGE_AGENTS))
    stage_check_parser.add_argument("--response")
    stage_check_parser.add_argument("--evidence", nargs="+")
    stage_check_parser.add_argument("--answers")
    stage_check_parser.add_argument("--candidate", help="auditor and reviser: the candidate that was audited")
    stage_check_parser.add_argument("--audit", help="reviser: the audit it answers")
    stage_check_parser.add_argument("--current")
    stage_check_parser.add_argument("--output")
    _run_options(stage_check_parser)
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
    catalog_extract_parser.add_argument("--current", help="catalog whose ids same-named concepts adopt")
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
    catalog_link_parser.add_argument("--topology")
    catalog_link_parser.add_argument("--answers", help="answered questionnaire draft with target values")
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
    catalog_approve_parser.add_argument("proposal", nargs="?")
    catalog_approve_parser.add_argument("--draft")
    catalog_approve_parser.add_argument("--current")
    catalog_approve_parser.add_argument("--reviewed", required=True, help="digest printed at the end of the review")
    catalog_approve_parser.add_argument("--by", required=True)
    catalog_approve_parser.add_argument("--at", help="approval time; with --run it defaults to now")
    catalog_approve_parser.add_argument("--output")
    catalog_approve_parser.add_argument("--run", help="run folder; derives proposal, draft, current and output")
    catalog_promote_parser = catalog_subparsers.add_parser(
        "promote", help="write an approved proposal into the canonical catalog"
    )
    catalog_promote_parser.add_argument("proposal", nargs="?")
    catalog_promote_parser.add_argument("--approval")
    catalog_promote_parser.add_argument("--draft")
    catalog_promote_parser.add_argument("--evidence", nargs="+")
    catalog_promote_parser.add_argument("--catalog", required=True)
    catalog_promote_parser.add_argument("--promotions", required=True)
    catalog_promote_parser.add_argument("--run", help="run folder; derives proposal, approval, draft and evidence")
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
    topology_infer_parser = topology_subparsers.add_parser(
        "infer-bindings", help="propose folder-convention bindings for unbound selections"
    )
    topology_infer_parser.add_argument("topology")
    topology_infer_parser.add_argument("--catalog", required=True)
    topology_infer_parser.add_argument("--output", required=True)

    run_parser = subparsers.add_parser("run", help="create and advance a run folder")
    run_subparsers = run_parser.add_subparsers(dest="run_command", required=True)
    run_init_parser = run_subparsers.add_parser("init", help="create a run folder for human-chosen applications")
    run_init_parser.add_argument("name", type=_repository_id)
    run_init_parser.add_argument("--topology", required=True)
    run_init_parser.add_argument("--application", required=True, action="append", type=_repository_id)
    run_init_parser.add_argument("--sources", default="sources.json")
    run_init_parser.add_argument("--respondent-id", default="technical-lead")
    run_init_parser.add_argument("--respondent-role", default="technical lead")
    run_init_parser.add_argument("--runs-dir", default=str(runs.RUNS_DIR))
    run_init_parser.add_argument("--created-at", help="ISO 8601 time with timezone; defaults to now")
    for name, text in (("check", "validate a run folder and list its artifacts"),
                       ("applications", "print the run's application ids as JSON"),
                       ("survey-check", "fail and name every application without a valid candidate"),
                       ("questionnaire", "build the questionnaire and write the answer sheet once"),
                       ("link", "add configuration-backed relationships and validate the proposal")):
        run_subparsers.add_parser(name, help=text).add_argument("run")
    run_discover_parser = run_subparsers.add_parser("discover", help="discover and select evidence for one application")
    run_discover_parser.add_argument("run")
    run_discover_parser.add_argument("--application", required=True, type=_repository_id)
    run_downgrade_parser = run_subparsers.add_parser(
        "downgrade", help="write the final candidate.json: lower every declined or unaddressed finding's claim")
    run_downgrade_parser.add_argument("run")
    run_downgrade_parser.add_argument("--application", required=True, type=_repository_id)
    run_record_parser = run_subparsers.add_parser("record", help="record the filled answer sheet")
    run_record_parser.add_argument("run")
    run_record_parser.add_argument("--recorded-at")
    run_review_parser = run_subparsers.add_parser("review", help="write the review and the run report")
    run_review_parser.add_argument("run")
    run_review_parser.add_argument("--catalog", default=str(runs.DEFAULT_CATALOG))
    return parser


def _run_options(parser):
    parser.add_argument("--run", help="run folder; derives every path from it (exclusive with explicit paths)")
    parser.add_argument("--application", type=_repository_id, help="with --run: the application (cartographer only)")


def _exclusive(args, explicit):
    given = [name for name in explicit if getattr(args, name) not in (None, [], False)]
    if args.run and given:
        raise ValueError("--run replaces {}; pass one or the other".format(", ".join("--" + name.replace("_", "-") for name in given)))


def _run_command(args):
    if args.run_command == "init":
        _write_json(runs.init_run(args.name, args.topology, args.application, args.sources, args.respondent_id,
                                  args.respondent_role, args.runs_dir, args.created_at))
        return 0
    if args.run_command == "check":
        result = runs.check_run(args.run)
        _write_json(result)
        return 0 if result["valid"] else 1
    if args.run_command == "applications":
        _write_json(runs.run_applications(args.run))
        return 0
    if args.run_command == "discover":
        _write_json(runs.discover_application(args.run, args.application))
        return 0
    if args.run_command == "downgrade":
        _write_json(runs.downgrade_application(args.run, args.application))
        return 0
    if args.run_command == "survey-check":
        result = runs.survey_failures(args.run)
        _write_json(result)
        if result["failed"]:
            print("error: the survey stops; no valid candidate for: {}".format(", ".join(result["failed"])), file=sys.stderr)
            return 1
        return 0
    if args.run_command == "questionnaire":
        _write_json(runs.build_questionnaire(args.run))
        return 0
    if args.run_command == "record":
        _write_json(runs.record_run_answers(args.run, args.recorded_at))
        return 0
    if args.run_command == "link":
        _write_json(runs.link_run(args.run))
        return 0
    if args.run_command == "review":
        result = runs.review_run(args.run, args.catalog)
        _write_json(result)
        return 0
    raise ValueError("unknown run command")


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
        if args.command == "questionnaire" and args.questionnaire_command == "bindings":
            inventories = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.inventory]
            bundles = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.evidence]
            pairing = None
            if args.topology and args.proposal:
                pairing = selection_pairs(json.loads(Path(args.topology).read_text(encoding="utf-8")),
                                          json.loads(Path(args.proposal).read_text(encoding="utf-8")))
            draft, skipped = generate_binding_questions(
                inventories, bundles, {"id": args.respondent_id, "role": args.respondent_role}, pairing)
            if draft is None:
                _write_json({"questions": 0, "skippedWithoutEvidence": skipped})
                return 0
            _write_json(draft, output=args.output)
            _write_json({"questions": len(draft["questions"]), "skippedWithoutEvidence": skipped})
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

        if args.command == "stage" and args.stage_command == "prepare" and args.run:
            _exclusive(args, ["output_dir", "evidence", "candidate", "answers", "analyzed_at", "audit"])
            _write_json(runs.prepare_run_stage(args.run, args.stage, args.application, getattr(args, "current", None)))
            return 0
        if args.command == "stage" and args.stage_command == "prepare":
            if not (args.output_dir and args.evidence):
                raise ValueError("stage prepare needs --run, or --output-dir and --evidence")
            _write_json(prepare_stage(args.stage, args.output_dir, args.evidence, args.candidate, args.answers,
                                      args.analyzed_at, args.respondent_id, args.respondent_role,
                                      args.audit))
            return 0
        if args.command == "stage" and args.stage_command == "run":
            if args.run:
                _exclusive(args, ["stage_dir", "output"])
                if not args.stage:
                    raise ValueError("stage run --run needs --stage")
                args.stage_dir, args.output = runs.run_stage_dir(args.run, args.stage, args.application)
            elif not (args.stage_dir and args.output):
                raise ValueError("stage run needs --run and --stage, or a stage directory and --output")
            outcome = run_stage(args.stage_dir, args.output, args.timeout, max_ai_credits=args.max_ai_credits,
                                model=args.model)
            if args.run and args.stage in runs.APPLICATION_STAGES:
                outcome["attempt"] = runs.record_attempt(args.run, args.stage, args.application)
            _write_json(outcome)
            if outcome["capReached"]:
                print("error: the Copilot credit cap ({}) was reached or the usage report is incomplete; the "
                      "response is not trusted".format(outcome["usage"]["cap"]), file=sys.stderr)
                return CAP_EXIT_CODE
            return 0 if outcome["exitCode"] == 0 else 1
        if args.command == "stage" and args.stage_command == "usage":
            _write_json(usage_totals(args.stage_dirs))
            return 0
        if args.command == "stage" and args.stage_command == "check" and args.run:
            _exclusive(args, ["response", "evidence", "answers", "output", "candidate", "audit"])
            document, errors, output = runs.check_run_stage(args.run, args.stage, args.application, args.current)
            result = {"valid": not errors, "errors": errors, "output": str(output) if not errors else None}
            if errors and args.stage in runs.APPLICATION_STAGES:
                # The workflow routes on this: after MAX_ATTEMPTS invalid outputs the application fails.
                attempts = runs.attempts_made(args.run, args.stage, args.application)
                result.update(attempts=attempts, exhausted=attempts >= runs.MAX_ATTEMPTS)
            _write_json(result)
            return 0 if not errors else 1
        if args.command == "stage" and args.stage_command == "check":
            if not (args.response and args.evidence and args.output):
                raise ValueError("stage check needs --run, or --response, --evidence and --output")
            current = json.loads(Path(args.current).read_text(encoding="utf-8")) if args.current else None
            load = lambda path: json.loads(Path(path).read_text(encoding="utf-8")) if path else None  # noqa: E731
            document, errors = check_stage(args.stage, Path(args.response).read_text(encoding="utf-8"), args.evidence,
                                           args.answers, current, load(args.candidate), load(args.audit))
            if document is not None:
                _write_json(document, output=args.output)
            _write_json({"valid": not errors, "errors": errors, "output": args.output if not errors else None})
            return 0 if not errors else 1
        if args.command == "catalog" and args.catalog_command == "extract":
            current = json.loads(Path(args.current).read_text(encoding="utf-8")) if args.current else None
            if current is not None and validate_landscape_catalog(current):
                raise ValueError("--current is not a valid catalog")
            _write_json(extract_catalog_proposal(Path(args.response).read_text(encoding="utf-8"), current), output=args.output)
            return 0
        if args.command == "catalog" and args.catalog_command == "propose-validate":
            proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
            draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
            bundles = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.evidence]
            errors = validate_catalog_proposal(proposal, draft, bundles) or convention_errors(proposal)
            _write_json({"valid": not errors, "errors": errors})
            return 0 if not errors else 1
        if args.command == "catalog" and args.catalog_command == "link-config":
            proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
            inventories = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.inventory]
            bundles = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.evidence]
            topology = json.loads(Path(args.topology).read_text(encoding="utf-8")) if args.topology else None
            answers = json.loads(Path(args.answers).read_text(encoding="utf-8")) if args.answers else None
            if answers is not None:
                errors = validate_questionnaire_draft(answers)
                if errors:
                    raise ValueError("answers draft is invalid: " + "; ".join(errors[:3]))
            _write_json(propose_shared_resource_relationships(proposal, inventories, bundles, args.analyzed_at, topology, answers),
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

        if args.command == "catalog" and args.catalog_command == "approve" and args.run:
            _exclusive(args, ["proposal", "draft", "output"])
            run = runs.Run(args.run)
            args.proposal, args.draft, args.output = run.linked_proposal, run.answers, run.approval
            args.current = args.current or str(runs.DEFAULT_CATALOG)
            args.at = args.at or runs.now()
        if args.command == "catalog" and args.catalog_command == "approve":
            if not (args.proposal and args.draft and args.output and args.at):
                raise ValueError("catalog approve needs --run, or a proposal with --draft, --at and --output")
            proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
            draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
            current = json.loads(Path(args.current).read_text(encoding="utf-8")) if args.current and Path(args.current).exists() else None
            approval = build_approval(proposal, current, draft, args.reviewed, args.by, args.at)
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(serialize(approval), encoding="utf-8")
            return 0
        if args.command == "catalog" and args.catalog_command == "promote" and args.run:
            _exclusive(args, ["proposal", "approval", "draft", "evidence"])
            run = runs.Run(args.run)
            args.proposal, args.approval, args.draft = run.linked_proposal, run.approval, run.answers
            args.evidence = [str(path) for path in run.all_evidence()]
        if args.command == "catalog" and args.catalog_command == "promote":
            if not (args.proposal and args.approval and args.draft and args.evidence):
                raise ValueError("catalog promote needs --run, or a proposal with --approval, --draft and --evidence")
            proposal = json.loads(Path(args.proposal).read_text(encoding="utf-8"))
            draft = json.loads(Path(args.draft).read_text(encoding="utf-8"))
            approval = json.loads(Path(args.approval).read_text(encoding="utf-8"))
            bundles = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.evidence]
            _write_json(promote(proposal, draft, bundles, approval, Path(args.catalog), Path(args.promotions), args.dry_run))
            return 0

        if args.command == "run":
            return _run_command(args)
        if args.command == "topology" and args.topology_command == "infer-bindings":
            topology = json.loads(Path(args.topology).read_text(encoding="utf-8"))
            catalog = json.loads(Path(args.catalog).read_text(encoding="utf-8"))
            errors = validate_landscape_catalog(catalog)
            if errors:
                raise ValueError("; ".join(errors))
            result = infer_bindings(topology, catalog)
            errors = validate_source_topology(result, None, catalog)
            if errors:
                raise ValueError("; ".join(errors))
            _write_json(result, output=args.output)
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
