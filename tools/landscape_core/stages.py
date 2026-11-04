"""Prepare, run and check the model-backed stages so a workflow only sequences them.

A stage stages read-only inputs in one directory, hands a rendered prompt to one Copilot agent, and checks the
response deterministically. Nothing here decides what the pipeline does next.
"""

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

from .candidates import extract_candidate_response, validate_candidate
from .catalog_ids import convention_errors
from .catalog_proposals import extract_catalog_proposal, validate_catalog_proposal
from .contracts import validate_evidence_bundle, validate_questionnaire_draft
from .questionnaires import assemble_questionnaire

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "workflows" / "prompts"
AGENTS = ROOT / ".github" / "agents"
STAGE_AGENTS = {"cartographer": "repository-cartographer", "curator-business": "questionnaire-curator",
                "synthesis": "catalog-synthesizer"}
# Fixed on purpose: read-only view tool, no built-in MCP servers, no temp-dir access, no questions to a human.
COPILOT_FLAGS = ("--available-tools=view", "--disable-builtin-mcps", "--disallow-temp-dir", "--no-ask-user", "--silent")


def render(template, values):
    text = (PROMPTS / (template + ".txt")).read_text(encoding="utf-8").strip()
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    leftover = re.findall(r"\{\{\w+\}\}", text)
    if leftover:
        raise ValueError("prompt template {} has unfilled fields: {}".format(template, ", ".join(sorted(set(leftover)))))
    return text


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _bundles(paths):
    bundles = {}
    for path in paths:
        bundle = _load(path)
        errors = validate_evidence_bundle(bundle)
        if errors:
            raise ValueError("{}: {}".format(path, "; ".join(errors[:3])))
        bundles[bundle["id"]] = bundle
    return bundles


def _scope(bundles):
    return [{"repository": bundle["repository"], "commit": bundle["commit"], "evidenceBundleId": bundle["id"]}
            for bundle in sorted(bundles.values(), key=lambda item: item["repository"])]


def _shape(bundles, respondent_id, respondent_role):
    return json.dumps({
        "schemaVersion": 1, "respondent": {"id": respondent_id, "role": respondent_role}, "scope": _scope(bundles),
        "questions": [{"id": "human-<slug>", "reviewType": "human-only", "answerStatus": "unanswered",
                       "wording": "<question>", "whyCodeCannotSettle": "<why>",
                       "links": [{"type": "claim", "claimId": "<id of a claim in that repository's candidate>",
                                  "repository": "<id>", "commit": "<sha>", "evidenceBundleId": "<bundle id>"}]}]},
        sort_keys=True)


def prepare_stage(stage, output_dir, evidence, candidates=(), answers=None, analyzed_at=None,
                  respondent_id="technical-lead", respondent_role="technical lead"):
    """Stage the inputs and the rendered prompt of one stage under ``output_dir`` and describe them."""
    if stage not in STAGE_AGENTS:
        raise ValueError("unknown stage {}".format(stage))
    bundles = _bundles(evidence)
    if stage == "cartographer" and len(bundles) != 1:
        raise ValueError("the cartographer analyzes exactly one evidence bundle")
    if stage in {"curator-business", "synthesis"} and not candidates:
        raise ValueError("{} needs a validated candidate for each repository".format(stage))
    if stage == "synthesis" and not (answers and analyzed_at):
        raise ValueError("synthesis needs --answers and --analyzed-at")
    if stage in {"cartographer", "synthesis"} and not analyzed_at:
        raise ValueError("{} needs --analyzed-at".format(stage))
    target = Path(output_dir)
    if target.exists() and any(target.iterdir()):
        raise ValueError("{} is not empty; stage inputs are never mixed".format(target))
    inputs = target / "inputs"
    inputs.mkdir(parents=True)
    files = {}
    for bundle in bundles.values():
        name = bundle["repository"] + ".evidence.json"
        (inputs / name).write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        files[bundle["id"]] = name
    candidate_names = []
    for path in candidates:
        candidate = _load(path)
        bundle = bundles.get(candidate.get("evidenceBundleId"))
        if bundle is None:
            raise ValueError("{} cites an evidence bundle that was not supplied".format(path))
        errors = validate_candidate(candidate, bundle)
        if errors:
            raise ValueError("{} is not a valid candidate: {}".format(path, "; ".join(errors[:3])))
        name = bundle["repository"] + ".candidate.json"
        (inputs / name).write_text(json.dumps(candidate, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        candidate_names.append(name)
    if answers:
        draft = _load(answers)
        errors = validate_questionnaire_draft(draft)
        if errors:
            raise ValueError("answers draft is invalid: " + "; ".join(errors[:3]))
        (inputs / "answers.json").write_text(json.dumps(draft, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    refer = lambda names: ", ".join("inputs/" + name for name in sorted(names))  # noqa: E731
    values = {"analyzed_at": analyzed_at or "", "evidence": refer(files.values()), "candidates": refer(candidate_names),
              "respondent_role": respondent_role, "shape": _shape(bundles, respondent_id, respondent_role)}
    if stage == "cartographer":
        values["evidence"] = next(iter(files.values()))
    prompt = render(stage, values)
    agent = STAGE_AGENTS[stage]
    (target / ".github" / "agents").mkdir(parents=True)
    shutil.copyfile(AGENTS / (agent + ".agent.md"), target / ".github" / "agents" / (agent + ".agent.md"))
    (target / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
    digests = ["{}  {}".format(hashlib.sha256(path.read_bytes()).hexdigest(), path.relative_to(target).as_posix())
               for path in sorted(inputs.iterdir())]
    (target / "inputs.sha256").write_text("\n".join(digests) + "\n", encoding="utf-8")
    return {"stage": stage, "agent": agent, "stageDir": str(target), "promptFile": str(target / "prompt.txt"),
            "inputs": sorted(path.name for path in inputs.iterdir())}


def copilot_command(stage_dir, executable="copilot"):
    """Build the Copilot invocation for a prepared stage; refuses a directory that was not prepared."""
    stage_dir = Path(stage_dir).resolve()
    prompt_file = stage_dir / "prompt.txt"
    agents = sorted((stage_dir / ".github" / "agents").glob("*.agent.md"))
    if not prompt_file.is_file() or len(agents) != 1 or not (stage_dir / "inputs").is_dir():
        raise ValueError("{} is not a prepared stage directory".format(stage_dir))
    agent = agents[0].name[:-len(".agent.md")]
    if agent not in STAGE_AGENTS.values():
        raise ValueError("unknown agent {}".format(agent))
    return [executable, "-C", str(stage_dir), "--agent", agent, *COPILOT_FLAGS,
            "--prompt", prompt_file.read_text(encoding="utf-8").strip()]


def run_stage(stage_dir, output, timeout=290, executable="copilot"):
    """Run the prepared stage's agent once and capture its response; return the process outcome."""
    command = copilot_command(stage_dir, executable)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        code, response, errors = completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired:
        code, response, errors = -1, "", "timed out after {}s".format(timeout)
    target.write_text(response, encoding="utf-8")
    target.with_suffix(".stderr.txt").write_text(errors, encoding="utf-8")
    return {"exitCode": code, "response": str(target), "responseBytes": len(response.encode("utf-8"))}


def check_stage(stage, response, evidence, answers=None, current=None):
    """Return (document or None, errors) for a captured response."""
    if stage not in STAGE_AGENTS:
        raise ValueError("unknown stage {}".format(stage))
    bundles = _bundles(evidence)
    try:
        if stage == "cartographer":
            candidate = extract_candidate_response(response)
            bundle = bundles.get(candidate.get("evidenceBundleId")) if isinstance(candidate, dict) else None
            errors = validate_candidate(candidate, bundle) if bundle else ["candidate cites an evidence bundle that was not supplied"]
            return (candidate if not errors else None), errors
        if stage == "curator-business":
            draft = assemble_questionnaire([("response", response)])
            scope = {item["evidenceBundleId"] for item in draft["scope"]}
            missing = sorted(set(bundles) - scope)
            errors = ["draft scope omits evidence bundle {}".format(item) for item in missing]
            return (draft if not errors else None), errors
        draft = _load(answers) if answers else None
        if draft is None:
            raise ValueError("synthesis check needs --answers")
        proposal = extract_catalog_proposal(response, current)
        errors = validate_catalog_proposal(proposal, draft, list(bundles.values())) or convention_errors(proposal)
        return (proposal if not errors else None), errors
    except ValueError as error:
        return None, [str(error)]
