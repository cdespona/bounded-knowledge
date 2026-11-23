"""Run folders: one human-chosen set of applications, pinned and laid out at fixed paths.

A run lives in ``work/runs/<date>-<name>/``. ``run.json`` is written once by ``init`` and is the only list of
applications; every other artifact sits at a path derived from the run and an application or selection id, so
workflows pass nothing but the run folder (decisions in issues #10 and #14). Each derived output records the
digest of its inputs next to it, so a rerun skips work whose inputs are unchanged.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from .audit import downgrade_candidate, finding_kinds
from .binding_questions import generate_binding_questions
from .catalog_ids import convention_errors
from .catalog_proposals import render_catalog_review, validate_catalog_proposal
from .config_links import propose_shared_resource_relationships
from .contracts import validate_landscape_catalog, validate_questionnaire_draft, validate_source_registry, \
    validate_source_topology
from .discovery import discover
from .evidence import select_evidence
from .git_repository import commit_sha, require_repository_root
from .questionnaires import answers_template, answers_template_markdown, assemble_questionnaire, \
    parse_answers_markdown, record_answers, render_questionnaire
from .sources import resolve_source
from .candidates import validate_candidate
from .stages import AGENTS, AUDIT_STAGES, BUDGETS, PROMPTS, STAGE_AGENTS, check_stage, prepare_stage, usage_totals

ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = ROOT / "work" / "runs"
DEFAULT_CATALOG = ROOT / "catalog" / "landscape-catalog.json"
SCHEMA_VERSION = 1
IDENTIFIER = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")
SELECTION_KINDS = {"application", "kubernetes", "terraform"}
STAMP_SUFFIX = ".inputs.sha256"
APPLICATION_STAGES = ("cartographer", "auditor", "reviser")  # run in this order, once per application
MAX_ATTEMPTS = 3  # an application fails when its auditor or reviser output is still invalid after this many runs
ATTEMPTS_FILE = "attempts"


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def run_manifest_errors(document):
    """Structural checks of ``run.json``; mirrors ``schemas/run-manifest.schema.json``."""
    if not isinstance(document, dict):
        return ["run manifest must be an object"]
    errors = []
    expected = {"schemaVersion", "name", "createdAt", "respondent", "applications"}
    if set(document) != expected:
        errors.append("run manifest fields must be exactly {}".format(", ".join(sorted(expected))))
    if document.get("schemaVersion") != SCHEMA_VERSION:
        errors.append("schemaVersion must be {}".format(SCHEMA_VERSION))
    name = document.get("name")
    if not isinstance(name, str) or not re.match(r"^\d{4}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*$", name):
        errors.append("name must be <date>-<identifier>")
    created = document.get("createdAt")
    if not isinstance(created, str) or not TIMESTAMP.match(created):
        errors.append("createdAt must be an ISO 8601 time with a timezone")
    respondent = document.get("respondent")
    if not isinstance(respondent, dict) or set(respondent) != {"id", "role"} or not all(
            isinstance(respondent.get(key), str) and respondent.get(key).strip() for key in ("id", "role")):
        errors.append("respondent must have a non-empty id and role")
    applications = document.get("applications")
    if not isinstance(applications, list) or not applications:
        return errors + ["applications must be a non-empty list"]
    seen = []
    for index, application in enumerate(applications):
        prefix = "applications[{}]".format(index)
        if not isinstance(application, dict) or set(application) != {"id", "selections"}:
            errors.append("{} must have exactly id and selections".format(prefix))
            continue
        if not isinstance(application["id"], str) or not IDENTIFIER.match(application["id"]):
            errors.append("{}.id must match {}".format(prefix, IDENTIFIER.pattern))
        seen.append(application["id"])
        selections = application["selections"]
        if not isinstance(selections, list) or not selections:
            errors.append("{}.selections must be a non-empty list".format(prefix))
            continue
        kinds = []
        for position, selection in enumerate(selections):
            where = "{}.selections[{}]".format(prefix, position)
            if not isinstance(selection, dict) or set(selection) != {"id", "kind", "repository", "subpath"}:
                errors.append("{} must have exactly id, kind, repository and subpath".format(where))
                continue
            for key in ("id", "repository"):
                if not isinstance(selection[key], str) or not IDENTIFIER.match(selection[key]):
                    errors.append("{}.{} must match {}".format(where, key, IDENTIFIER.pattern))
            if selection["kind"] not in SELECTION_KINDS:
                errors.append("{}.kind is invalid".format(where))
            if not isinstance(selection["subpath"], str) or not selection["subpath"]:
                errors.append("{}.subpath must be a non-empty string".format(where))
            kinds.append(selection["kind"])
        if kinds.count("application") != 1:
            errors.append("{} must have exactly one application selection".format(prefix))
        ids = [item.get("id") for item in selections if isinstance(item, dict)]
        if ids != sorted(set(ids), key=ids.index) or ids != sorted(ids):
            errors.append("{}.selections must be sorted by id without duplicates".format(prefix))
    if seen != sorted(set(seen)):
        errors.append("applications must be sorted by id without duplicates")
    return errors


class Run:
    """A loaded run folder and the fixed paths inside it."""

    def __init__(self, root):
        self.root = Path(root)
        manifest_path = self.root / "run.json"
        if self.root.is_symlink() or not manifest_path.is_file():
            raise ValueError("{} is not a run folder (no run.json)".format(self.root))
        self.manifest = _load(manifest_path)
        errors = run_manifest_errors(self.manifest)
        if errors:
            raise ValueError("run.json is invalid: " + "; ".join(errors))
        for name in ("topology.json", "sources.json"):
            if not (self.root / name).is_file():
                raise ValueError("{} is missing {}".format(self.root, name))
        self.applications = {item["id"]: item for item in self.manifest["applications"]}

    # Inputs frozen by init.
    @property
    def topology_path(self):
        return self.root / "topology.json"

    @property
    def sources_path(self):
        return self.root / "sources.json"

    @property
    def created_at(self):
        return self.manifest["createdAt"]

    @property
    def respondent(self):
        return dict(self.manifest["respondent"])

    def application(self, application_id):
        if application_id not in self.applications:
            raise ValueError("application {} is not part of this run".format(application_id))
        return self.applications[application_id]

    def application_selection(self, application_id):
        return next(item for item in self.application(application_id)["selections"] if item["kind"] == "application")

    # Survey, per application and per selection.
    def application_dir(self, application_id):
        self.application(application_id)
        return self.root / "survey" / application_id

    def inventory(self, application_id, selection_id):
        return self.application_dir(application_id) / selection_id / "inventory.json"

    def evidence(self, application_id, selection_id):
        return self.application_dir(application_id) / selection_id / "evidence.json"

    def discovery(self, application_id):
        return self.application_dir(application_id) / "discovery.json"

    def cartographer_candidate(self, application_id):
        return self.application_dir(application_id) / "cartographer-candidate.json"

    def audit(self, application_id):
        return self.application_dir(application_id) / "audit.json"

    def revision(self, application_id):
        return self.application_dir(application_id) / "revision.json"

    def candidate(self, application_id):
        """The final candidate (revised and downgraded); the only per-application file the curator reads."""
        return self.application_dir(application_id) / "candidate.json"

    def selection_pairs(self):
        """Every (application, selection) of the run, in a stable order."""
        return [(application, selection) for application in sorted(self.applications)
                for selection in self.applications[application]["selections"]]

    def application_evidence(self):
        return [self.evidence(application, self.application_selection(application)["id"])
                for application in sorted(self.applications)]

    def all_inventories(self):
        return [self.inventory(application, selection["id"]) for application, selection in self.selection_pairs()]

    def all_evidence(self):
        return [self.evidence(application, selection["id"]) for application, selection in self.selection_pairs()]

    # Whole set.
    def stage_dir(self, stage, application_id=None):
        if stage in APPLICATION_STAGES:
            if application_id is None:
                raise ValueError("the {} stage needs --application".format(stage))
            return self.application_dir(application_id) / stage
        if application_id is not None:
            raise ValueError("{} covers the whole run; drop --application".format(stage))
        return self.root / {"curator-business": "survey/curator", "synthesis": "synthesis/stage"}[stage]

    @property
    def bindings_draft(self):
        return self.root / "survey" / "bindings-draft.json"

    @property
    def curator_draft(self):
        return self.root / "survey" / "curator-draft.json"

    @property
    def questionnaire(self):
        return self.root / "survey" / "questionnaire.json"

    @property
    def questionnaire_markdown(self):
        return self.root / "survey" / "questionnaire.md"

    @property
    def answers_sheet(self):
        return self.root / "answers" / "answers.md"

    @property
    def answers(self):
        return self.root / "answers" / "answers.json"

    @property
    def proposal(self):
        return self.root / "synthesis" / "proposal.json"

    @property
    def linked_proposal(self):
        return self.root / "synthesis" / "linked-proposal.json"

    @property
    def review(self):
        return self.root / "review" / "review.md"

    @property
    def report(self):
        return self.root / "review" / "run-report.md"

    @property
    def approval(self):
        return self.root / "review" / "approval.json"


# Skip-on-rerun: an output is fresh when it exists and the digest of its inputs is the one recorded beside it.

def inputs_digest(paths):
    digest = hashlib.sha256()
    for path in paths:
        path = Path(path)
        digest.update(path.name.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b"missing")
        digest.update(b"\0")
    return digest.hexdigest()


def _stamp(output):
    output = Path(output)
    return output.with_name(output.name + STAMP_SUFFIX)


def is_fresh(output, inputs):
    output, stamp = Path(output), _stamp(output)
    if not output.is_file() or not stamp.is_file() or not all(Path(path).is_file() for path in inputs):
        return False
    return stamp.read_text(encoding="utf-8").strip() == inputs_digest(inputs)


def write_with_stamp(output, inputs, document=None, text=None):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if text is not None:
        output.write_text(text, encoding="utf-8")
    else:
        _write(output, document)
    _stamp(output).write_text(inputs_digest(inputs) + "\n", encoding="utf-8")


# init, check, applications.

def init_run(name, topology_path, applications, sources_path, respondent_id="technical-lead",
             respondent_role="technical lead", runs_dir=RUNS_DIR, created_at=None):
    """Create ``<runs_dir>/<date>-<name>/`` with run.json and frozen copies of the topology and sources."""
    if not IDENTIFIER.match(name or ""):
        raise ValueError("run name must match {}".format(IDENTIFIER.pattern))
    created_at = created_at or now()
    if not TIMESTAMP.match(created_at):
        raise ValueError("--created-at must be an ISO 8601 time with a timezone")
    requested = sorted(set(applications))
    if not requested or len(requested) != len(applications):
        raise ValueError("name each application once")
    for application in requested:
        if not IDENTIFIER.match(application):
            raise ValueError("application id {} must match {}".format(application, IDENTIFIER.pattern))
    topology = _load(topology_path)
    errors = validate_source_topology(topology)
    if errors:
        raise ValueError("topology is invalid: " + "; ".join(errors[:3]))
    registry = _load(sources_path)
    errors = validate_source_registry(registry)
    if errors:
        raise ValueError("sources registry is invalid: " + "; ".join(errors[:3]))
    enabled = {item["id"]: item for item in registry["repositories"] if item["enabled"]}
    selections = {item["id"]: item for item in topology["sourceSelections"]}
    entries, used = [], set()
    for application in requested:
        bound = sorted({binding["sourceSelectionId"] for binding in topology["bindings"]
                        if binding["target"] == {"type": "application", "id": application}})
        if not bound:
            raise ValueError("no topology selection is bound to application {}".format(application))
        chosen = []
        for selection_id in bound:
            selection = selections[selection_id]
            if selection["repositoryId"] not in enabled:
                raise ValueError("selection {} uses repository {}, which is not enabled in the sources registry"
                                 .format(selection_id, selection["repositoryId"]))
            if selection["kind"] == "application" and selection["subpath"] != ".":
                raise ValueError("selection {}: an application selection must cover its whole repository "
                                 "(subpath \".\")".format(selection_id))
            chosen.append({"id": selection_id, "kind": selection["kind"], "repository": selection["repositoryId"],
                           "subpath": selection["subpath"]})
            used.add(selection["repositoryId"])
        if [item["kind"] for item in chosen].count("application") != 1:
            raise ValueError("application {} needs exactly one application selection".format(application))
        entries.append({"id": application, "selections": chosen})
    folder_name = "{}-{}".format(created_at[:10], name)
    target = Path(runs_dir) / folder_name
    if target.exists():
        raise ValueError("{} exists; a run's application set never changes, start a new run".format(target))
    manifest = {"schemaVersion": SCHEMA_VERSION, "name": folder_name, "createdAt": created_at,
                "respondent": {"id": respondent_id, "role": respondent_role}, "applications": entries}
    errors = run_manifest_errors(manifest)
    if errors:
        raise ValueError("; ".join(errors))
    for part in ("survey", "answers", "synthesis", "review"):
        (target / part).mkdir(parents=True)
    _write(target / "topology.json", topology)
    _write(target / "sources.json", {"schemaVersion": registry["schemaVersion"],
                                     "repositories": [enabled[item] for item in sorted(used)]})
    _write(target / "run.json", manifest)
    return {"run": str(target), "applications": requested}


def check_run(run_path):
    """Validate the run folder and report which fixed-path artifacts exist so far."""
    run = Run(run_path)
    errors = validate_source_topology(_load(run.topology_path))
    errors += validate_source_registry(_load(run.sources_path))
    present = {}
    for application in sorted(run.applications):
        present[application] = {"discovery": run.discovery(application).is_file(),
                                "cartographerCandidate": run.cartographer_candidate(application).is_file(),
                                "audit": run.audit(application).is_file(),
                                "revision": run.revision(application).is_file(),
                                "candidate": run.candidate(application).is_file()}
    for name in ("questionnaire", "answers", "proposal", "linked_proposal", "review", "approval"):
        present[name] = getattr(run, name).is_file()
    return {"run": str(run.root), "valid": not errors, "errors": errors, "present": present}


def run_applications(run_path):
    return {"applications": sorted(Run(run_path).applications)}


# Survey: discovery and evidence per selection, pinned once.

def _pinned_elsewhere(run, application):
    pins = {}
    for other in sorted(run.applications):
        if other != application and run.discovery(other).is_file():
            for item in _load(run.discovery(other))["selections"]:
                pins[item["repository"]] = (item["commit"], other)
    return pins


def discover_application(run_path, application):
    """Discover and select evidence for every selection of one application; commits are pinned on first run."""
    run = Run(run_path)
    selections = run.application(application)["selections"]
    files = [path for selection in selections
             for path in (run.inventory(application, selection["id"]), run.evidence(application, selection["id"]))]
    if run.discovery(application).is_file() and all(path.is_file() for path in files):
        return {"application": application, "skipped": True, "reason": "already discovered at pinned commits"}
    topology = _load(run.topology_path)
    pinned = _pinned_elsewhere(run, application)
    record = []
    for selection in selections:
        resolution = resolve_source(str(run.sources_path), selection["repository"])
        root = Path(resolution["resolvedPath"])
        if selection["repository"] in pinned and pinned[selection["repository"]][0] != resolution["commit"]:
            commit, other = pinned[selection["repository"]]
            raise ValueError("repository {} is at {} but {} pinned it at {}; it moved during the survey, start a "
                             "new run".format(selection["repository"], resolution["commit"], other, commit))
        if selection["kind"] == "application":
            inventory = discover(root, selection["repository"])
        else:
            inventory = discover(root, selection["repository"], topology=topology, selection=selection["id"])
        bundle = select_evidence(inventory, root, purpose="semantic")
        _write(run.inventory(application, selection["id"]), inventory)
        _write(run.evidence(application, selection["id"]), bundle)
        record.append({"id": selection["id"], "kind": selection["kind"], "repository": selection["repository"],
                       "commit": inventory["commit"]})
    _write(run.discovery(application), {"schemaVersion": 1, "application": application, "selections": record})
    return {"application": application, "skipped": False, "selections": record}


def survey_failures(run_path):
    """Applications without a final, up-to-date candidate. The survey stops if any are listed.

    Each stage's output is stamped with the digest of its inputs, so an up-to-date final candidate also proves
    the cartographer, auditor and reviser outputs it was derived from are the current ones."""
    run = Run(run_path)
    failed = []
    for application in sorted(run.applications):
        inputs, output = downgrade_inputs(run, application)
        if not is_fresh(output, inputs):
            failed.append(application)
    return {"failed": failed}


# Model stages driven by the run folder.

def stage_inputs(run, stage, application=None, current=None):
    """Return (prepare kwargs, inputs for the freshness digest, output path) of a model stage in a run."""
    stage_dir = run.stage_dir(stage, application)
    code = [PROMPTS / (stage + ".txt"), AGENTS / (STAGE_AGENTS[stage] + ".agent.md"), BUDGETS]
    if stage in APPLICATION_STAGES:
        evidence = [run.evidence(application, run.application_selection(application)["id"])]
        if stage == "cartographer":
            kwargs = {"evidence": evidence, "analyzed_at": run.created_at}
            return dict(kwargs, output_dir=stage_dir), evidence + code, run.cartographer_candidate(application)
        candidate = [run.cartographer_candidate(application)]
        if stage == "auditor":
            return {"evidence": evidence, "candidates": candidate, "output_dir": stage_dir}, \
                evidence + candidate + code, run.audit(application)
        return {"evidence": evidence, "candidates": candidate, "audit": run.audit(application),
                "output_dir": stage_dir}, evidence + candidate + [run.audit(application)] + code, run.revision(application)
    evidence = run.application_evidence()
    candidates = [run.candidate(item) for item in sorted(run.applications)]
    kwargs = {"evidence": evidence, "candidates": candidates, "respondent_id": run.respondent["id"],
              "respondent_role": run.respondent["role"], "output_dir": stage_dir}
    if stage == "curator-business":
        return kwargs, evidence + candidates + code, run.curator_draft
    kwargs.update(answers=run.answers, analyzed_at=run.created_at)
    extra = [Path(current)] if current and Path(current).is_file() else []
    return kwargs, evidence + candidates + [run.answers] + extra + code, run.proposal


def _reset_stage_dir(run, stage_dir):
    stage_dir = Path(stage_dir)
    if not stage_dir.exists():
        return
    stage_dir.resolve().relative_to(run.root.resolve())  # raises if the stage folder escaped the run
    if stage_dir.is_symlink() or not (stage_dir / "inputs.sha256").is_file():
        raise ValueError("{} exists but is not a stage folder this run prepared; refusing to clear it".format(stage_dir))
    shutil.rmtree(stage_dir)


def prepare_run_stage(run_path, stage, application=None, current=None):
    run = Run(run_path)
    kwargs, inputs, output = stage_inputs(run, stage, application, current)
    if is_fresh(output, inputs):
        return {"stage": stage, "upToDate": True, "output": str(output)}
    missing = [str(path) for path in inputs if not Path(path).is_file()]
    if missing:
        raise ValueError("{} cannot start; missing inputs: {}".format(stage, ", ".join(missing)))
    _reset_stage_dir(run, kwargs["output_dir"])
    outcome = prepare_stage(stage, **kwargs)
    return dict(outcome, upToDate=False, output=str(output))


def run_stage_dir(run_path, stage, application=None):
    run = Run(run_path)
    stage_dir = run.stage_dir(stage, application)
    return stage_dir, stage_dir / "response.txt"


def check_run_stage(run_path, stage, application=None, current=None):
    """Check a captured response; on success write the stage's fixed output and its input digest."""
    run = Run(run_path)
    kwargs, inputs, output = stage_inputs(run, stage, application, current)
    response = run.stage_dir(stage, application) / "response.txt"
    if not response.is_file():
        return None, ["no captured response at {}".format(response)], output
    current_catalog = _load(current) if current and Path(current).is_file() else None
    audited = {}
    if stage in AUDIT_STAGES:
        audited["candidate"] = _load(run.cartographer_candidate(application))
        if stage == "reviser":
            audited["audit"] = _load(run.audit(application))
    document, errors = check_stage(stage, response.read_text(encoding="utf-8"), [str(path) for path in kwargs["evidence"]],
                                   str(run.answers) if stage == "synthesis" else None, current_catalog, **audited)
    if document is not None:
        write_with_stamp(output, inputs, document)
    return document, errors, output


def record_attempt(run_path, stage, application):
    """Count one Copilot invocation of a per-application stage; ``prepare`` resets the count with the folder."""
    stage_dir = Run(run_path).stage_dir(stage, application)
    marker = stage_dir / ATTEMPTS_FILE
    count = int(marker.read_text(encoding="utf-8").strip() or 0) + 1 if marker.is_file() else 1
    marker.write_text("{}\n".format(count), encoding="utf-8")
    return count


def attempts_made(run_path, stage, application):
    marker = Run(run_path).stage_dir(stage, application) / ATTEMPTS_FILE
    return int(marker.read_text(encoding="utf-8").strip() or 0) if marker.is_file() else 0


# Downgrade: the last deterministic step of an application's cartographer sub-workflow.

def downgrade_inputs(run, application):
    evidence = run.evidence(application, run.application_selection(application)["id"])
    return [evidence, run.cartographer_candidate(application), run.audit(application),
            run.revision(application)], run.candidate(application)


def _audit_documents(run, application):
    missing = [str(path) for path in downgrade_inputs(run, application)[0] if not path.is_file()]
    if missing:
        raise ValueError("cannot downgrade {}; missing: {}".format(application, ", ".join(missing)))
    bundle = _load(run.evidence(application, run.application_selection(application)["id"]))
    return bundle, _load(run.cartographer_candidate(application)), _load(run.audit(application)), \
        _load(run.revision(application))


def downgrade_application(run_path, application):
    """Apply the deterministic downgrade to the revised candidate and write the final ``candidate.json``."""
    run = Run(run_path)
    inputs, output = downgrade_inputs(run, application)
    if is_fresh(output, inputs):
        return {"application": application, "skipped": True, "candidate": str(output)}
    bundle, original, audit, revision = _audit_documents(run, application)
    final, summary = downgrade_candidate(original, audit, revision)
    errors = validate_candidate(final, bundle, finding_kinds(audit))
    if errors:
        raise ValueError("downgraded candidate is invalid: " + "; ".join(errors[:3]))
    write_with_stamp(output, inputs, final)
    return dict(summary, application=application, skipped=False, candidate=str(output))


def audit_summary(run, application):
    """Findings, decisions, downgrades and caveats of one application, recomputed deterministically; None if absent."""
    if not all(path.is_file() for path in downgrade_inputs(run, application)[0]):
        return None
    _bundle, original, audit, revision = _audit_documents(run, application)
    return downgrade_candidate(original, audit, revision)[1]


def evidence_audit_lines(run):
    lines = ["", "## Evidence audit", ""]
    rows = [(application, audit_summary(run, application)) for application in sorted(run.applications)]
    if not any(summary for _application, summary in rows):
        return lines + ["No application has an audit."]
    lines += ["| Application | Findings | Applied | Declined | Applied but unchanged | Downgrades | Caveats | Unattached |",
              "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for application, summary in rows:
        if summary is None:
            lines.append("| {} | not audited | | | | | | |".format(application))
        else:
            lines.append("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                application, summary["findings"], summary["applied"], summary["declined"], summary["unaddressed"],
                summary["downgrades"], summary["caveats"], ", ".join(summary["unattached"]) or "0"))
    return lines + ["", "Declined and unchanged findings lower the claim one status step and leave a caveat in "
                    "`survey/<application>/candidate.json`; \"Unattached\" findings name no claim, so nothing was "
                    "lowered."]


# Questionnaire, answers, linking and review.

def build_questionnaire(run_path):
    """Merge binding questions and the curator draft, render it, and write the answer sheet once."""
    run = Run(run_path)
    inventories, bundles = run.all_inventories(), run.all_evidence()
    inputs = inventories + bundles + [run.curator_draft, run.topology_path, run.root / "run.json"]
    missing = [str(path) for path in inputs if not Path(path).is_file()]
    if missing:
        raise ValueError("questionnaire cannot be built; missing: {}".format(", ".join(missing)))
    result = {"skipped": False}
    if is_fresh(run.questionnaire, inputs):
        result["skipped"] = True
    else:
        draft, skipped = generate_binding_questions([_load(path) for path in inventories],
                                                    [_load(path) for path in bundles], run.respondent)
        parts = [(str(run.curator_draft), run.curator_draft.read_text(encoding="utf-8"))]
        if draft is not None:
            _write(run.bindings_draft, draft)
            parts.append((str(run.bindings_draft), run.bindings_draft.read_text(encoding="utf-8")))
        elif run.bindings_draft.exists():
            run.bindings_draft.unlink()
        questionnaire = assemble_questionnaire(parts)
        write_with_stamp(run.questionnaire, inputs, questionnaire)
        run.questionnaire_markdown.write_text(render_questionnaire(questionnaire), encoding="utf-8")
        result["bindingQuestions"] = len(draft["questions"]) if draft else 0
        result["skippedWithoutEvidence"] = skipped
    questionnaire = _load(run.questionnaire)
    if run.answers_sheet.is_file():
        sheet = parse_answers_markdown(run.answers_sheet.read_text(encoding="utf-8"))
        if sheet.get("draftId") != questionnaire["id"]:
            raise ValueError("{} answers an older questionnaire; move it aside to get a new sheet".format(run.answers_sheet))
        result["answersSheet"] = "kept"
    else:
        run.answers_sheet.parent.mkdir(parents=True, exist_ok=True)
        run.answers_sheet.write_text(answers_template_markdown(answers_template(questionnaire)), encoding="utf-8")
        result["answersSheet"] = "written"
    result.update(questionnaire=str(run.questionnaire), questions=len(questionnaire["questions"]),
                  sheet=str(run.answers_sheet))
    return result


def record_run_answers(run_path, recorded_at=None):
    run = Run(run_path)
    inputs = [run.questionnaire, run.answers_sheet]
    if is_fresh(run.answers, inputs):
        return {"skipped": True, "answers": str(run.answers)}
    for path in inputs:
        if not path.is_file():
            raise ValueError("cannot record answers; missing {}".format(path))
    sheet = parse_answers_markdown(run.answers_sheet.read_text(encoding="utf-8"))
    answered = record_answers(_load(run.questionnaire), sheet, recorded_at)
    errors = validate_questionnaire_draft(answered)
    if errors:
        raise ValueError("recorded answers are invalid: " + "; ".join(errors[:3]))
    write_with_stamp(run.answers, inputs, answered)
    return {"skipped": False, "answers": str(run.answers)}


def link_run(run_path):
    """Add configuration-backed relationships to the synthesis proposal and validate the result."""
    run = Run(run_path)
    inventories, bundles = run.all_inventories(), run.all_evidence()
    inputs = [run.proposal, run.answers, run.topology_path] + inventories + bundles
    if is_fresh(run.linked_proposal, inputs):
        return {"skipped": True, "linkedProposal": str(run.linked_proposal)}
    for path in inputs:
        if not Path(path).is_file():
            raise ValueError("cannot link; missing {}".format(path))
    answers = _load(run.answers)
    loaded = [_load(path) for path in bundles]
    linked = propose_shared_resource_relationships(_load(run.proposal), [_load(path) for path in inventories], loaded,
                                                   run.created_at, _load(run.topology_path), answers)
    errors = validate_catalog_proposal(linked, answers, loaded) or convention_errors(linked)
    if errors:
        raise ValueError("linked proposal is invalid: " + "; ".join(errors[:5]))
    write_with_stamp(run.linked_proposal, inputs, linked)
    return {"skipped": False, "linkedProposal": str(run.linked_proposal)}


def _moved(root, pinned, current):
    try:
        completed = subprocess.run(["git", "-C", str(root), "rev-list", "--count", "{}..{}".format(pinned, current)],
                                   capture_output=True, text=True, timeout=30, check=True)
        return "{} commits".format(completed.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return "an unknown number of commits"


def commit_drift(run):
    """Per application, the selections whose repository HEAD has moved past the pinned commit."""
    registry = {item["id"]: item for item in _load(run.sources_path)["repositories"]}
    drift, heads = {}, {}
    for application in sorted(run.applications):
        if not run.discovery(application).is_file():
            continue
        for item in _load(run.discovery(application))["selections"]:
            repository = item["repository"]
            if repository not in heads:
                try:
                    root = require_repository_root(Path(registry[repository]["path"]))
                    heads[repository] = (root, commit_sha(root))
                except Exception as error:  # noqa: BLE001 - an unreachable repository is reported, not fatal
                    heads[repository] = (None, "unavailable: {}".format(error))
            root, head = heads[repository]
            if head != item["commit"]:
                moved = _moved(root, item["commit"], head) if root is not None else head
                drift.setdefault(application, []).append(
                    {"selection": item["id"], "repository": repository, "pinned": item["commit"], "head": head,
                     "moved": moved})
    return drift


def review_run(run_path, catalog=DEFAULT_CATALOG):
    """Write the digest-bound catalog review and a separate run report (drift warnings, usage totals)."""
    run = Run(run_path)
    for path in (run.linked_proposal, run.answers):
        if not path.is_file():
            raise ValueError("cannot review; missing {}".format(path))
    proposal = _load(run.linked_proposal)
    current = _load(catalog) if catalog and Path(catalog).is_file() else None
    errors = validate_landscape_catalog(proposal) + (validate_landscape_catalog(current) if current else [])
    if errors:
        raise ValueError("; ".join(errors[:5]))
    run.review.parent.mkdir(parents=True, exist_ok=True)
    run.review.write_text(render_catalog_review(proposal, current, _load(run.answers)), encoding="utf-8")
    drift = commit_drift(run)
    stage_dirs = [run.stage_dir(stage, item) for item in sorted(run.applications) for stage in APPLICATION_STAGES]
    stage_dirs += [run.stage_dir("curator-business"), run.stage_dir("synthesis")]
    usage = usage_totals([path for path in stage_dirs if path.is_dir()])
    lines = ["# Run report: {}".format(run.manifest["name"]), "",
             "Read this next to `review.md`. It is not part of the approved digest.", "", "## Commit drift", ""]
    if not drift:
        lines.append("Every repository is still at the commit the survey pinned.")
    for application, items in sorted(drift.items()):
        for item in items:
            lines.append("- WARNING {}: selection `{}` ({}) moved {} past pinned `{}` (now `{}`). This review "
                         "describes the pinned code; analyzing newer code needs a new survey."
                         .format(application, item["selection"], item["repository"], item["moved"],
                                 item["pinned"][:12], item["head"][:12]))
    lines += evidence_audit_lines(run)
    lines += ["", "## Copilot usage", "", "```json", json.dumps(usage, indent=2, sort_keys=True), "```", ""]
    run.report.write_text("\n".join(lines), encoding="utf-8")
    return {"review": str(run.review), "report": str(run.report), "drift": drift}
