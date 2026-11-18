"""Prepare, run and check the model-backed stages so a workflow only sequences them.

A stage stages read-only inputs in one directory, hands a rendered prompt to one Copilot agent, and checks the
response deterministically. Nothing here decides what the pipeline does next.
"""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess

from .candidates import extract_candidate_response, validate_candidate
from .catalog_ids import convention_errors
from .catalog_proposals import extract_catalog_proposal, validate_catalog_proposal
from .contracts import validate_evidence_bundle, validate_questionnaire_draft
from .questionnaires import assemble_questionnaire
from .safety import COPILOT_CONFIGURATION_DIRECTORIES, COPILOT_INSTRUCTION_NAMES

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / "workflows" / "prompts"
BUDGETS = ROOT / "workflows" / "stage-budgets.json"
AGENTS = ROOT / ".github" / "agents"
STAGE_AGENTS = {"cartographer": "repository-cartographer", "curator-business": "questionnaire-curator",
                "synthesis": "catalog-synthesizer"}
# Fixed on purpose: read-only view tool, no built-in MCP servers, no temp-dir access, no questions to a human.
COPILOT_FLAGS = ("--available-tools=view", "--disable-builtin-mcps", "--disallow-temp-dir", "--no-ask-user", "--silent")
# Anything Copilot (or a sibling agent tool) could load as instructions, agents, prompts, skills, hooks or MCP servers.
# Broader than the source-registry list on purpose: a stage directory should hold nothing of the kind but our agent.
CUSTOMIZATION_DIRECTORIES = tuple(COPILOT_CONFIGURATION_DIRECTORIES) + (
    ".github/prompts", ".github/chatmodes", ".github/hooks", ".github/copilot", ".copilot", ".claude", ".agents",
    ".vscode", ".cursor", ".gemini")
CUSTOMIZATION_NAMES = set(COPILOT_INSTRUCTION_NAMES) | {"mcp.json", ".mcp.json", "mcp-config.json"}
NANO_PER_CREDIT = 1_000_000_000  # unconfirmed: the report gives totalNanoAiu; 1 AI credit = 1e9 nano is assumed
USAGE_FILE = "usage.json"
CAP_EXIT_CODE = 3
CUSTOMIZATION_SUFFIXES = (".instructions.md", ".prompt.md", ".agent.md", ".chatmode.md")


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


def _is_customization(relative):
    path = PurePosixPath(relative)
    if path.name in CUSTOMIZATION_NAMES or path.name.endswith(CUSTOMIZATION_SUFFIXES):
        return True
    if relative == ".github/copilot-instructions.md":
        return True
    return any(relative == directory or relative.startswith(directory + "/") for directory in CUSTOMIZATION_DIRECTORIES)


def _enclosing_repository(directory):
    for candidate in (directory, *directory.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def stage_customization_errors(stage_dir):
    """List what could steer Copilot in a stage directory besides our own, unmodified agent file.

    Copilot only sees the stage directory, so this replaces a manual review of customizations. It reads
    names and compares bytes; it never interprets the files.
    """
    stage_dir = Path(stage_dir)
    if stage_dir.is_symlink():
        return ["the stage directory must not be a symbolic link"]
    stage_dir = stage_dir.resolve()
    errors = []
    repository = _enclosing_repository(stage_dir)
    if repository is not None and repository != ROOT:
        errors.append("stage directory lies inside another repository ({}), whose customizations Copilot "
                      "may load".format(repository))
    allowed = {".github/agents/{}.agent.md".format(agent) for agent in STAGE_AGENTS.values()}
    for current, directory_names, file_names in os.walk(stage_dir):
        current_path = Path(current)
        for name in sorted(directory_names + file_names):
            path = current_path / name
            relative = path.relative_to(stage_dir).as_posix()
            if path.is_symlink():
                errors.append("symbolic link in the stage directory: {}".format(relative))
            elif path.is_file() and _is_customization(relative):
                if relative not in allowed:
                    errors.append("foreign customization file: {}".format(relative))
                elif path.read_bytes() != (AGENTS / name).read_bytes():
                    errors.append("agent file differs from the observatory's copy: {}".format(relative))
        directory_names[:] = [name for name in directory_names if not (current_path / name).is_symlink()]
    return sorted(errors)


def load_budgets(path=BUDGETS):
    """Read and validate the checked-in per-stage budgets: {minimum, {stage: (model or None, maxAiCredits)}}."""
    document = _load(path)
    minimum = document.get("minMaxAiCredits")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 30:
        raise ValueError("{}: minMaxAiCredits must be an integer of at least 30".format(path))
    stages = document.get("stages")
    if not isinstance(stages, dict) or set(stages) != set(STAGE_AGENTS):
        raise ValueError("{}: stages must configure exactly {}".format(path, ", ".join(sorted(STAGE_AGENTS))))
    budgets = {}
    for stage, entry in stages.items():
        credits, model = entry.get("maxAiCredits") if isinstance(entry, dict) else None, entry.get("model") if isinstance(entry, dict) else None
        if set(entry) - {"maxAiCredits", "model"} or not isinstance(credits, int) or isinstance(credits, bool) \
                or credits < minimum or not (model is None or (isinstance(model, str) and model)):
            raise ValueError("{}: invalid budget for {}".format(path, stage))
        budgets[stage] = (model, credits)
    return minimum, budgets


def stage_of(stage_dir):
    """The stage name of a prepared stage directory, from its one agent file."""
    agents = sorted((Path(stage_dir) / ".github" / "agents").glob("*.agent.md"))
    names = {agent: stage for stage, agent in STAGE_AGENTS.items()}
    if len(agents) != 1 or agents[0].name[:-len(".agent.md")] not in names:
        raise ValueError("{} is not a prepared stage directory".format(stage_dir))
    return names[agents[0].name[:-len(".agent.md")]]


def resolve_budget(stage_dir, max_ai_credits=None, model=None):
    """Return (model or None, maxAiCredits) from the checked-in budget plus overrides; enforces the minimum."""
    minimum, budgets = load_budgets()
    default_model, default_credits = budgets[stage_of(stage_dir)]
    credits = default_credits if max_ai_credits is None else max_ai_credits
    if not isinstance(credits, int) or isinstance(credits, bool) or credits < minimum:
        raise ValueError("--max-ai-credits must be an integer of at least {}".format(minimum))
    if model is not None and not model.strip():
        raise ValueError("--model must not be empty")
    return (model or default_model), credits


def copilot_command(stage_dir, executable="copilot", max_ai_credits=None, model=None):
    """Build the Copilot invocation for a prepared stage; refuses a directory that was not prepared or that
    carries any customization besides its one agent file."""
    stage_dir = Path(stage_dir)
    errors = stage_customization_errors(stage_dir)
    if errors:
        raise ValueError("{} is not safe to hand to Copilot: {}".format(stage_dir, "; ".join(errors)))
    stage_dir = stage_dir.resolve()
    prompt_file = stage_dir / "prompt.txt"
    agents = sorted((stage_dir / ".github" / "agents").glob("*.agent.md"))
    if not prompt_file.is_file() or len(agents) != 1 or not (stage_dir / "inputs").is_dir():
        raise ValueError("{} is not a prepared stage directory".format(stage_dir))
    agent = agents[0].name[:-len(".agent.md")]
    if agent not in STAGE_AGENTS.values():
        raise ValueError("unknown agent {}".format(agent))
    model, credits = resolve_budget(stage_dir, max_ai_credits, model)
    budget = ["--max-ai-credits", str(credits), "--usage-output-file", str(stage_dir / USAGE_FILE)]
    if model:
        budget += ["--model", model]
    return [executable, "-C", str(stage_dir), "--agent", agent, *COPILOT_FLAGS, *budget,
            "--prompt", prompt_file.read_text(encoding="utf-8").strip()]


def read_usage(path):
    """Parse a Copilot usage report defensively. Returns {"status", "credits", "models", "notes"}.

    status is "complete" (Copilot says all credits were counted), "incomplete" (it says otherwise),
    "missing", "malformed" or "unrecognized" (no known fields). Nothing is guessed: unknown shapes give
    credits None and a note.
    """
    path = Path(path)
    if not path.is_file():
        return {"status": "missing", "credits": None, "models": {}, "notes": ["usage file was not written"]}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {"status": "malformed", "credits": None, "models": {}, "notes": ["usage file is not valid JSON"]}
    metrics = document.get("modelMetrics") if isinstance(document, dict) else None
    total = document.get("totalNanoAiu") if isinstance(document, dict) else None
    number = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0  # noqa: E731
    if not isinstance(metrics, dict) or not number(total) or not all(
            isinstance(item, dict) and number(item.get("totalNanoAiu")) for item in metrics.values()):
        return {"status": "unrecognized", "credits": None, "models": {},
                "notes": ["usage file lacks totalNanoAiu or modelMetrics.<model>.totalNanoAiu"]}
    models = {name: round(item["totalNanoAiu"] / NANO_PER_CREDIT, 3) for name, item in sorted(metrics.items())}
    reported = document.get("aiCreditsStatus")
    statuses = {reported} | {item.get("aiCreditsStatus") for item in metrics.values()}
    notes = []
    if statuses != {"complete"}:
        notes.append("aiCreditsStatus is {}, not complete".format(", ".join(sorted(str(item) for item in statuses))))
    return {"status": "complete" if not notes else "incomplete", "credits": round(total / NANO_PER_CREDIT, 3),
            "models": models, "notes": notes}


def run_stage(stage_dir, output, timeout=290, executable="copilot", max_ai_credits=None, model=None):
    """Run the prepared stage's agent once, capture its response and usage; return the outcome.

    The outcome's capReached is true when the usage report shows the credit cap was used up or its credit
    status is not "complete", or when Copilot failed and said so; such a response is never a success.
    """
    command = copilot_command(stage_dir, executable, max_ai_credits, model)
    used_model, cap = resolve_budget(stage_dir, max_ai_credits, model)
    usage_file = Path(stage_dir).resolve() / USAGE_FILE
    usage_file.unlink(missing_ok=True)  # a stale report from an earlier attempt must not be read
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        code, response, errors = completed.returncode, completed.stdout, completed.stderr
    except subprocess.TimeoutExpired:
        code, response, errors = -1, "", "timed out after {}s".format(timeout)
    target.write_text(response, encoding="utf-8")
    target.with_suffix(".stderr.txt").write_text(errors, encoding="utf-8")
    usage = read_usage(usage_file)
    said_so = code != 0 and re.search(r"credit|usage limit|budget", errors, re.IGNORECASE) is not None
    reached = said_so or usage["status"] == "incomplete" or (
        usage["credits"] is not None and usage["credits"] >= cap)
    summary = {"creditsUsed": usage["credits"], "cap": cap, "model": used_model, "models": usage["models"],
               "status": usage["status"], "notes": usage["notes"], "file": str(usage_file) if usage_file.is_file() else None}
    return {"exitCode": code, "response": str(target), "responseBytes": len(response.encode("utf-8")),
            "usage": summary, "capReached": reached}


def usage_totals(stage_dirs):
    """Sum the usage reports of prepared stage directories by stage and model; stable, sorted output."""
    stages, problems, total = {}, [], 0.0
    for stage_dir in stage_dirs:
        stage = stage_of(stage_dir)
        usage = read_usage(Path(stage_dir) / USAGE_FILE)
        if usage["credits"] is None:
            problems.append({"stageDir": str(stage_dir), "status": usage["status"], "notes": usage["notes"]})
            continue
        if usage["notes"]:
            problems.append({"stageDir": str(stage_dir), "status": usage["status"], "notes": usage["notes"]})
        entry = stages.setdefault(stage, {"runs": 0, "credits": 0.0, "models": {}})
        entry["runs"] += 1
        entry["credits"] = round(entry["credits"] + usage["credits"], 3)
        for name, credits in usage["models"].items():
            entry["models"][name] = round(entry["models"].get(name, 0.0) + credits, 3)
        total = round(total + usage["credits"], 3)
    return {"stages": {name: stages[name] for name in sorted(stages)}, "totalCredits": total,
            "problems": sorted(problems, key=lambda item: item["stageDir"])}


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
