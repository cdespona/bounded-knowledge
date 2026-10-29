"""Deterministic handling of untrusted questionnaire drafts."""

import datetime
import json
from pathlib import Path
import re

from .candidates import extract_candidate_response
from .config_files import mask_account_ids
from .contracts import artifact_id, validate_questionnaire_draft

LINK_TARGETS = {"claim": "claimId", "gap": "gap", "observation": "observationId"}


def link_target(link):
    return link[LINK_TARGETS[link["type"]]]


def extract_questionnaire_response(response):
    draft = extract_candidate_response(response)
    draft.pop("id", None)
    draft["id"] = artifact_id(draft)
    return draft


def assemble_questionnaire(responses, return_batches=False):
    """Deterministically merge bounded batch responses into one validated draft.

    ``responses`` is a sequence of ``(label, text)`` pairs. Each text must hold exactly one
    complete draft-shaped JSON object (without an ``id``) that validates on its own, is
    entirely unanswered, and agrees with the other batches on respondent and scope pins.
    Any incomplete, conflicting, or duplicated input rejects the whole assembly.
    With ``return_batches`` the result is ``(draft, [(label, extracted_batch), ...])``.
    """
    responses = list(responses)
    if not responses:
        raise ValueError("at least one batch response is required")
    errors, batches = [], []
    for label, text in responses:
        try:
            batch = extract_questionnaire_response(text)
        except ValueError as error:
            errors.append("{}: {}".format(label, error)); continue
        batch_errors = validate_questionnaire_draft(batch)
        for question in batch.get("questions", []) if isinstance(batch.get("questions"), list) else []:
            if isinstance(question, dict) and (question.get("answerStatus") != "unanswered" or "answer" in question):
                batch_errors.append("model batches must not contain answers")
                break
        if batch_errors:
            errors.extend("{}: {}".format(label, error) for error in batch_errors)
        else:
            batches.append((label, batch))
    if errors:
        raise ValueError("; ".join(errors))

    respondent = batches[0][1]["respondent"]
    pins, question_owner, questions = {}, {}, []
    for label, batch in batches:
        if batch["respondent"] != respondent:
            errors.append("{}: respondent differs from {}".format(label, batches[0][0]))
        for item in batch["scope"]:
            pin = (item["commit"], item["evidenceBundleId"])
            if pins.setdefault(item["repository"], (pin, label))[0] != pin:
                errors.append("{}: scope pin for {} conflicts with {}".format(
                    label, item["repository"], pins[item["repository"]][1]))
        for question in batch["questions"]:
            if question["id"] in question_owner:
                errors.append("{}: question id {} is duplicated from {}".format(
                    label, question["id"], question_owner[question["id"]]))
            question_owner.setdefault(question["id"], label)
            questions.append(question)
    if errors:
        raise ValueError("; ".join(errors))

    draft = {
        "schemaVersion": 1,
        "respondent": respondent,
        "scope": [{"repository": repository, "commit": pin[0], "evidenceBundleId": pin[1]}
                  for repository, (pin, _label) in sorted(pins.items())],
        "questions": sorted(questions, key=lambda question: question["id"]),
    }
    draft["id"] = artifact_id(draft)
    final_errors = validate_questionnaire_draft(draft)
    if final_errors:
        raise ValueError("assembled draft is invalid: {}".format("; ".join(final_errors)))
    if return_batches:
        return draft, [(label, batch) for label, batch in batches]
    return draft


def plan_extracted_files(batches, directory):
    """Return ``[(path, content)]`` for ``<input stem>.extracted.json`` files, writing nothing.

    Rejects duplicate output names and existing files whose bytes would differ.
    """
    planned, owners = [], {}
    for label, batch in batches:
        name = Path(label).stem + ".extracted.json"
        if name in owners:
            raise ValueError("{} and {} would both write {}".format(owners[name], label, name))
        owners[name] = label
        target = Path(directory) / name
        content = json.dumps(batch, indent=2, sort_keys=True) + "\n"
        if target.exists() and (not target.is_file() or target.read_text(encoding="utf-8") != content):
            raise ValueError("refusing to overwrite differing file: {}".format(target))
        planned.append((target, content))
    return planned


def _link_keys(draft, review_type):
    return {tuple(sorted(link.items())) for question in draft["questions"]
            if question["reviewType"] == review_type for link in question["links"]}


def consolidate_questionnaire(original, response):
    """Accept a model's merge of overlapping questions only if it is lossless.

    ``original`` is a validated, entirely unanswered draft; ``response`` is the model text
    holding one proposed replacement draft. Per review type the proposal must keep exactly
    the original links (none dropped, none added), the same respondent and scope, and may
    not add questions. Whether the merged wording is faithful remains for human review.
    """
    errors = validate_questionnaire_draft(original)
    if errors:
        raise ValueError("original draft is invalid: {}".format("; ".join(errors)))
    if any(item["answerStatus"] != "unanswered" for item in original["questions"]):
        raise ValueError("only an entirely unanswered draft can be consolidated")
    proposed = extract_questionnaire_response(response)
    errors = validate_questionnaire_draft(proposed)
    if any(item.get("answerStatus") != "unanswered" or "answer" in item for item in proposed.get("questions", [])
           if isinstance(item, dict)):
        errors.append("model consolidation must not contain answers")
    if not errors:
        if proposed["respondent"] != original["respondent"]:
            errors.append("respondent differs from the original")
        if sorted(map(lambda i: tuple(sorted(i.items())), proposed["scope"])) != sorted(
                map(lambda i: tuple(sorted(i.items())), original["scope"])):
            errors.append("scope differs from the original")
        for review_type in ("human-only", "code-answerable"):
            before, after = _link_keys(original, review_type), _link_keys(proposed, review_type)
            for key in sorted(before - after):
                errors.append("{} link dropped: {}".format(review_type, link_target(dict(key))))
            for key in sorted(after - before):
                errors.append("{} link added: {}".format(review_type, link_target(dict(key))))
            count = lambda draft: sum(1 for item in draft["questions"] if item["reviewType"] == review_type)
            if count(proposed) > count(original):
                errors.append("{} questions increased".format(review_type))
    if errors:
        raise ValueError("; ".join(errors))
    proposed["questions"] = sorted(proposed["questions"], key=lambda item: item["id"])
    proposed.pop("id")
    proposed["id"] = artifact_id(proposed)
    final_errors = validate_questionnaire_draft(proposed)
    if final_errors:
        raise ValueError("consolidated draft is invalid: {}".format("; ".join(final_errors)))
    return proposed


ANSWER_STATUSES = {"human-only": ("answered", "unknown", "deferred"),
                   "code-answerable": ("accepted", "rejected", "deferred")}


def _applies_to(question):
    """Group a question's links by repository so a reader sees what it is about."""
    grouped = {}
    for link in question["links"]:
        grouped.setdefault(link["repository"], []).append("{} {}".format(link["type"], link_target(link)))
    return [{"repository": repository, "targets": sorted(targets)} for repository, targets in sorted(grouped.items())]


def answers_template(draft, review_type="human-only"):
    """Return an empty, human-fillable sheet for the unanswered questions of one review type."""
    if review_type not in ANSWER_STATUSES:
        raise ValueError("review type must be one of {}".format(", ".join(sorted(ANSWER_STATUSES))))
    errors = validate_questionnaire_draft(draft)
    if errors:
        raise ValueError("invalid questionnaire draft: {}".format("; ".join(errors)))
    return {
        "draftId": draft["id"],
        "reviewType": review_type,
        "answerer": "",
        "answers": [dict({"questionId": item["id"], "wording": item["wording"], "appliesTo": _applies_to(item),
                          "answerStatus": "unanswered", "text": ""},
                         **({"values": []} if item.get("expects") == "target-values" else {}))
                    for item in draft["questions"]
                    if item["reviewType"] == review_type and item["answerStatus"] == "unanswered"],
    }


def record_answers(draft, sheet, recorded_at=None):
    """Apply an attributed answer sheet to a draft; reject anything ambiguous.

    Entries left ``unanswered`` are skipped. Answered, unknown, and deferred entries keep the
    exact text. The tool stamps the recording time (UTC) unless one is supplied.
    """
    errors = validate_questionnaire_draft(draft)
    if errors:
        raise ValueError("invalid questionnaire draft: {}".format("; ".join(errors)))
    if not isinstance(sheet, dict) or set(sheet) - {"draftId", "reviewType", "answerer", "answers"} or not isinstance(sheet.get("answers"), list):
        raise ValueError("answer sheet has invalid fields")
    if sheet.get("draftId") != draft["id"]:
        raise ValueError("answer sheet was made for a different draft")
    answerer = sheet.get("answerer")
    if not isinstance(answerer, str) or not answerer.strip():
        raise ValueError("answerer must be a non-empty string")
    recorded_at = recorded_at or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    questions = {item["id"]: item for item in draft["questions"]}
    result, seen, problems = copy_draft(draft), set(), []
    updated = {item["id"]: item for item in result["questions"]}
    for entry in sheet["answers"]:
        if not isinstance(entry, dict) or set(entry) - {"questionId", "wording", "appliesTo", "answerStatus", "text", "values"}:
            problems.append("answer entry has invalid fields"); continue
        question_id = entry.get("questionId")
        question = questions.get(question_id)
        if question is None:
            problems.append("unknown question id {}".format(question_id)); continue
        if question_id in seen:
            problems.append("duplicate answer for {}".format(question_id)); continue
        seen.add(question_id)
        status = entry.get("answerStatus")
        if status == "unanswered":
            continue
        if sheet.get("reviewType", question["reviewType"]) != question["reviewType"]:
            problems.append("{} does not belong on a {} sheet".format(question_id, sheet["reviewType"]))
        elif question["answerStatus"] != "unanswered":
            problems.append("{} is already answered".format(question_id))
        elif "wording" in entry and entry["wording"] != question["wording"]:
            problems.append("{} wording was changed".format(question_id))
        elif status not in ANSWER_STATUSES[question["reviewType"]]:
            problems.append("{} has invalid answerStatus".format(question_id))
        else:
            values, problem = _normalized_values(question, status, entry)
            text = entry.get("text")
            if problem is None and values and (not isinstance(text, str) or not text.strip()):
                text = "Target values: " + "; ".join("{} = {}".format(item["profile"] or "default", item["value"]) for item in values)
            if problem is None and (not isinstance(text, str) or not text.strip()):
                problem = "needs non-empty text (write e.g. 'I do not know' for unknown)"
            if problem:
                problems.append("{} {}".format(question_id, problem))
                continue
            updated[question_id]["answerStatus"] = status
            updated[question_id]["answer"] = {"text": text, "answerer": answerer.strip(), "recordedAt": recorded_at}
            if values:
                updated[question_id]["answer"]["values"] = values
    if problems:
        raise ValueError("; ".join(problems))
    result.pop("id")
    result["id"] = artifact_id(result)
    final_errors = validate_questionnaire_draft(result)
    if final_errors:
        raise ValueError("recorded draft is invalid: {}".format("; ".join(final_errors)))
    return result


def _normalized_values(question, status, entry):
    """Return (sorted unique values with masked account ids, problem) for the values of one answer entry."""
    raw = entry.get("values") or []
    if question.get("expects") != "target-values" or status != "answered":
        return [], ("does not take values" if raw else None)
    if not isinstance(raw, list) or not raw:
        return [], "needs a value per environment under 'Values:'"
    values = set()
    for item in raw:
        if not isinstance(item, dict) or set(item) - {"profile", "value"} or not isinstance(item.get("value"), str):
            return [], "has an invalid value entry"
        profile = item.get("profile")
        profile = None if profile in (None, "", "default") else str(profile).lower()
        values.add((profile, mask_account_ids(item["value"].strip())))
    return [{"profile": profile, "value": value} for profile, value in sorted(values, key=lambda pair: (pair[0] or "", pair[1]))], None


def copy_draft(draft):
    return json.loads(json.dumps(draft))


def answers_template_markdown(sheet):
    """Render an answer sheet as a Markdown form for a human to fill in."""
    lines = ["# Questionnaire answer sheet", "",
             "Fill in `Answerer`, and for each question set `Status` to {},".format(
                 ", ".join(ANSWER_STATUSES[sheet.get("reviewType", "human-only")])),
             "(leave unanswered to skip) and write your {} under `Answer:`. Do not edit the headings.".format(
                 "reviewer note, with the reason for your decision," if sheet.get("reviewType") == "code-answerable" else "answer"), "",
             "Review type: {}".format(sheet.get("reviewType", "human-only")),
             "Draft: {}".format(sheet["draftId"]), "Answerer: {}".format(sheet["answerer"])]
    for entry in sheet["answers"]:
        lines.extend(["", "## {}".format(entry["questionId"]), "", "> {}".format(" ".join(entry["wording"].split())), "",
                      "Applies to {} application(s):".format(len(entry["appliesTo"]))]
                     + ["- `{}` — {}".format(item["repository"], "; ".join(item["targets"])) for item in entry["appliesTo"]]
                     + ["", "Status: {}".format(entry["answerStatus"])]
                     + (["Values (one `- environment: value` line each; `default` when it is the same everywhere):", "Values:", ""]
                        + ["- {}: {}".format(item["profile"] or "default", item["value"]) for item in entry["values"]]
                        + [""] if "values" in entry else [])
                     + ["Answer:", "", entry["text"]])
    return "\n".join(lines).rstrip("\n") + "\n"


def parse_answers_markdown(markdown):
    """Parse a filled Markdown answer sheet into the JSON answer-sheet structure.

    Answer text starts on the ``Answer:`` line or below it, with surrounding blank lines trimmed.
    """
    parts = re.split(r"(?m)^## (\S+)[ \t]*$", markdown)
    header = {}
    for line in parts[0].splitlines():
        for key in ("Draft", "Answerer"):
            if line.startswith(key + ":"):
                header[key] = line[len(key) + 1:].strip()
    if "Draft" not in header:
        raise ValueError("answer sheet is missing the 'Draft:' line")
    answers, problems = [], []
    for question_id, body in zip(parts[1::2], parts[2::2]):
        status, text, in_answer, values, in_values = None, [], False, [], False
        for line in body.splitlines():
            if in_answer:
                text.append(line)
            elif line.startswith("Values:"):
                in_values = True
            elif in_values and line.startswith("- "):
                match = re.match(r"^- ([A-Za-z0-9_.-]+):[ \t]+(\S.*)$", line)
                if match is None:
                    problems.append("{} has a malformed value line".format(question_id))
                else:
                    values.append({"profile": match.group(1), "value": match.group(2).strip()})
            elif line.startswith("Status:"):
                status = line[len("Status:"):].strip()
            elif line.startswith("Answer:"):
                in_answer = True
                if line[len("Answer:"):].strip():
                    text.append(line[len("Answer:"):].lstrip(" "))
        if status is None or not in_answer:
            problems.append("{} must have 'Status:' and 'Answer:' lines".format(question_id)); continue
        answer = "\n".join(text).strip("\n")
        if status == "unanswered" and answer.strip():
            problems.append("{} has answer text but status is unanswered".format(question_id)); continue
        answers.append(dict({"questionId": question_id, "answerStatus": status, "text": answer},
                            **({"values": values} if in_values else {})))
    if problems:
        raise ValueError("; ".join(problems))
    return {"draftId": header["Draft"], "answerer": header.get("Answerer", ""), "answers": answers}


def render_questionnaire(draft):
    errors = validate_questionnaire_draft(draft)
    if errors:
        raise ValueError("invalid questionnaire draft: {}".format("; ".join(errors)))
    lines = ["# Questionnaire", "", "**Respondent:** {} ({})".format(
        draft["respondent"]["id"], draft["respondent"]["role"]), "", "## Scope", ""]
    for item in draft["scope"]:
        lines.append("- `{}` at `{}` — evidence `{}`".format(
            item["repository"], item["commit"], item["evidenceBundleId"]))
    for review_type, heading in (("human-only", "Questions for the respondent"), ("code-answerable", "Code-answerable audit items")):
        items = [item for item in draft["questions"] if item["reviewType"] == review_type]
        if not items: continue
        lines.extend(["", "## " + heading, ""])
        for item in items:
            lines.extend(["### {} — {}".format(item["id"], item["answerStatus"]), "", item["wording"], ""])
            for link in item["links"]:
                target = link_target(link)
                lines.append("- Linked {}: `{}` in `{}` at `{}`".format(link["type"], target, link["repository"], link["commit"]))
            lines.append("- Why code cannot settle it: {}".format(item["whyCodeCannotSettle"]))
            if "answer" in item:
                answer = item["answer"]
                lines.append("- Recorded answer from {} at {}: {}".format(answer["answerer"], answer["recordedAt"], answer["text"]))
    return "\n".join(lines) + "\n"
