# Slice 20: evidence auditor, reviser and downgrade

Decision: issue #11 ("Do the evidence auditor and candidate reviser return to the pipeline, and where?").
Implementation: issue #30, on top of the run folders of slice 19.

Per application, inside the existing `for_each` of `workflows/survey.yaml`, `workflows/cartographer.yaml` runs

```text
cartographer -> check -> audit -> check -> reviser -> check -> downgrade
```

before the curator. Models only propose. A finding is a doubt, not a failure: findings never fail a run, however
many there are.

## Files

| File | Written by |
| --- | --- |
| `survey/<app>/cartographer-candidate.json` | `stage check cartographer --run` (unchanged validation; caveats are refused here) |
| `survey/<app>/audit.json` | `stage check auditor --run` |
| `survey/<app>/revision.json` | `stage check reviser --run`: decisions plus the whole revised candidate |
| `survey/<app>/candidate.json` | `run downgrade RUN --application ID`: the final candidate, the only file the curator reads |

Every output has its `<output>.inputs.sha256` stamp. The auditor's inputs are the evidence bundle, the
cartographer candidate, the prompt, the agent file and the budgets file; the reviser adds `audit.json`; the final
candidate's inputs are the bundle and the three documents. A change anywhere re-triggers everything downstream.

## Auditor

Agent `evidence-auditor`, prompt `workflows/prompts/auditor.txt`, a stronger default model than the cartographer
(`workflows/stage-budgets.json`; override with `stage run --model`). Output:

```json
{"schemaVersion": 1, "evidenceBundleId": "...", "findings": [
  {"id": "kebab-case", "kind": "overreach | mislabelled-status | missed-evidence", "explanation": "...",
   "claimId": "a claim of the candidate", "citations": [<bundle reference>], "contradicts": [<bundle reference>]}]}
```

A bundle reference is `{repository, commit, path, lines, observationId}` and must lie inside a selected bundle
entry. `check` requires: unique ids; `overreach` and `mislabelled-status` name a `claimId` that exists;
`missed-evidence` has `citations`; `contradicts` needs a `claimId`; every finding points at a claim or a bundle
path. An empty `findings` array is valid.

## Reviser

Agent `candidate-reviser`, prompt `workflows/prompts/reviser.txt`, the cartographer's model. Output:
`{schemaVersion, evidenceBundleId, decisions: [{findingId, decision: applied|declined, reason}], candidate}`.
The deterministic check enforces, then runs the cartographer's own candidate validation on `candidate`:

- every finding has exactly one decision with a reason, and no decision names an unknown finding;
- identity fields and `openQuestions` are unchanged and no claim carries `caveats`;
- a claim that is changed or dropped must be named by an applied finding, and its status is never raised;
- a claim may be added only if an applied `missed-evidence` finding cites one of the new claim's evidence references.

## Downgrade

`run downgrade` is deterministic. Per finding:

- an **applied** finding whose claim changed (or was dropped), or whose citation backs an added claim, is
  addressed and does nothing further;
- a **declined** finding, or an applied one that left its claim unchanged ("unaddressed"), lowers the named claim one
  step (`confirmed` → `inferred` → `unknown`; never below `unknown`), caps confidence (`medium`/`low`), adds a
  caveat `{findingId, kind, explanation}`, and, at `unknown`, a `missingEvidence` entry;
- contradicting locations in `contradicts` are added to the claim's `counterevidence`, whatever the decision;
- a finding that names no claim (missed evidence nobody added) has nothing to lower; it is counted as
  `unattached` in the report.

Two findings on one claim lower it twice. The result is validated with every caveat resolving into `audit.json`
(`findingId` exists and `kind` matches); `caveats` is an optional field of `schemas/claim.schema.json` and of the
catalog assessment in `schemas/landscape-catalog.schema.json`.

## Failure and the review

An application fails only when its auditor or reviser output (or the cartographer's) is still invalid after three
attempts. `stage run --run` counts attempts in `<stage folder>/attempts` (a fresh `prepare` resets them);
`stage check --run` prints `attempts` and `exhausted`, and the workflow ends the application's sub-workflow on
`exhausted`. `run survey-check` then names the application and the survey stops before the curator.

`review/run-report.md` has an "Evidence audit" table per application: findings, applied, declined, applied but
unchanged, downgrades, caveats, unattached. Usage totals include the auditor and reviser folders.

## Known gaps

- The auditor's default model name in `stage-budgets.json` has not been confirmed against Copilot's model list.
- Catalog `caveats` are shape-checked only; they resolve into `audit.json` where it exists (the candidate), not
  inside a promoted catalog (`S20-D04`).
- Synthesis output is not audited (`S20-D01`).
- `docs/initiative/conductor-flow.svg` still shows the one-stage cartographer.
