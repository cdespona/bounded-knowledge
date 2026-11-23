# Slice 19: run folders and the survey, synthesis and promotion workflows

A run covers a set of applications chosen by a human and moves through three Conductor workflows chained by files
in one folder. The two human gates (answering the questionnaire and approving the review digest) sit between
workflows, so a pause of any length needs no running process. Decisions: issues #10 and #14 (with its correction
comment). Implementation issue: #26.

```text
landscape run init NAME --topology FILE --application ID [--application ID ...]
                        [--sources sources.json] [--respondent-id ID] [--respondent-role ROLE]
                        [--runs-dir work/runs] [--created-at ISO8601]
conductor run workflows/survey.yaml    --input run=work/runs/<date>-<name>
  (a human fills in answers/answers.md)
conductor run workflows/synthesis.yaml --input run=work/runs/<date>-<name> [--input catalog=...]
  (a human reads review/review.md and review/run-report.md)
conductor run workflows/promotion.yaml --input run=work/runs/<date>-<name> [--input catalog=...] [--input promotions=...]
```

## The manifest

`run init` resolves each application id through the topology: every source selection bound to that application
(declared or inferred) belongs to it, and each selection's repository must be enabled in the sources registry. An
application needs exactly one selection of kind `application`, covering its whole repository (`subpath` `.`); its
Kubernetes and Terraform selections may be subpaths of shared repositories.

`run.json` (`schemas/run-manifest.schema.json`) is written once and never edited. It holds the folder name,
`createdAt` (used as every `analyzedAt` of the run), the questionnaire respondent and, per application, its sorted
selections (`id`, `kind`, `repository`, `subpath`). The topology and the registry entries the run uses are copied
next to it. `init` refuses an existing folder: changing the application set means a new run.

## Layout

| Path | Written by |
| --- | --- |
| `run.json`, `topology.json`, `sources.json` | `run init` |
| `survey/<app>/<selection>/inventory.json`, `evidence.json` | `run discover` (semantic evidence selection) |
| `survey/<app>/discovery.json` | `run discover`: the commit pinned for every selection |
| `survey/<app>/cartographer/`, `auditor/`, `reviser/` | the per-application stage folders (inputs, prompt, response, usage, attempts) |
| `survey/<app>/cartographer-candidate.json` | `stage check cartographer --run`: the validated, unaudited candidate |
| `survey/<app>/audit.json`, `revision.json` | `stage check auditor|reviser --run` (see slice 20) |
| `survey/<app>/candidate.json` | `run downgrade`: the final, revised and downgraded candidate; the only per-application file the curator reads |
| `survey/curator/`, `survey/curator-draft.json` | the curator stage |
| `survey/bindings-draft.json` | `run questionnaire` (runtime binding questions, when there are any) |
| `survey/questionnaire.json`, `questionnaire.md` | `run questionnaire` (bindings and curator drafts assembled) |
| `answers/answers.md` | `run questionnaire`, written once; the human fills it in |
| `answers/answers.json` | `run record`: the answered draft |
| `synthesis/stage/`, `synthesis/proposal.json` | the synthesis stage |
| `synthesis/linked-proposal.json` | `run link` (configuration links plus proposal validation) |
| `review/review.md` | `run review`: the digest-bound catalog review |
| `review/run-report.md` | `run review`: commit drift warnings and Copilot usage totals, outside the digest |
| `review/approval.json` | `catalog approve --run` |

A missing input is an error, never a shorter list. Application and selection ids match `^[a-z0-9]+(-[a-z0-9]+)*$`,
so they are always safe directory names.

## Rerun rules

Every derived output has a sibling `<output>.inputs.sha256` holding a digest of its inputs. For model stages the
inputs include the prompt template, the agent file and `workflows/stage-budgets.json`. `stage prepare --run`
prints `upToDate: true` when the output is fresh, and the workflow skips the stage, so a rerun after a partial
failure only calls Copilot for what is missing or changed. Changing an earlier output re-triggers everything
downstream of it.

- `run discover` never re-pins: once `discovery.json` exists the application keeps its commits. If a repository
  shared by two applications moves between their discoveries, the second one fails and asks for a new run.
- `run questionnaire` never overwrites `answers/answers.md`, and refuses a sheet that answers an older
  questionnaire.
- Synthesis always works at the pinned commits. `review/run-report.md` warns, per application, about every
  selection whose repository has moved past its pinned commit and by how many commits. Analyzing newer code
  needs a new survey.

## Workflows

- `survey.yaml`: check, list applications, then a `for_each` over `cartographer.yaml` (two at a time,
  `continue_on_error`), then `run survey-check`. That step fails, naming every application without a valid,
  up-to-date candidate, and the survey ends there without a questionnaire. Otherwise the curator stage runs and
  `run questionnaire` writes the questionnaire and the answer sheet.
- `cartographer.yaml` (inputs `run`, `application`): `run discover`, then cartographer, auditor and reviser, each
  prepare (skipped when fresh), run and check with up to three attempts, then `run downgrade` (slice 20). Exit code 3
  from `stage run` (credit cap) ends it.
- `synthesis.yaml` (inputs `run`, `catalog`): `run record`, the synthesis stage, `run link`, `run review`.
- `promotion.yaml` (inputs `run`, `catalog`, `promotions`): a digest gate, an approver-name gate (the safe option
  first in both, so `--skip-gates` never approves), `catalog approve --run`, which stamps the approval time, and
  `catalog promote --run`.

## Explicit paths stay available

`stage prepare|run|check`, `catalog approve` and `catalog promote` accept `--run` (with `--application` for the
cartographer, and `--stage` for `stage run`) as an alternative to their explicit path options; passing both is an
error. The deterministic steps that combine many lists (`questionnaire bindings`, `catalog link-config`,
`catalog propose-validate`) are reached through `run questionnaire` and `run link` instead of each taking `--run`.

## Answer provenance on promotion (#27)

`catalog promote` keeps the questionnaire answers the promoted change used, so a cleanup of `work/` cannot lose
them. The catalog and its promotions directory stay local and untracked while the repository is public.

- The answers go to `<promotions>/answers/<record stem>.json`, next to the promotion record
  (`<UTC time>-<proposal digest prefix>.json`), which names the file in `answersFile` and the question ids in
  `answerIds`. The file holds `schemaVersion`, `questionnaireDraftId` and `questions`: the answered questions
  (with their `answer`: text, answerer, `recordedAt`) that the proposal's interview evidence cites, sorted by id.
- Interview evidence in the promoted catalog is the existing curated shape (`kind` `curated`, `sourceType`
  `interview`, `sourceId` the question id, `suppliedBy`, `recordedAt`) with its optional `locator` set to
  `answers/<record stem>.json#<question id>`. The approved proposal is not edited; locators are added to the merged
  catalog, and the diff ignores them so that promoting an unchanged proposal stays a no-op.
- Evidence status rules are unchanged: testimony alone never confirms, and an interview locator proves who said what
  and when, not that the statement is true.
- Promotion validates every reference against the file it is about to write and aborts before writing when one does
  not resolve. `catalog validate CATALOG --promotions DIR` runs the same check later: each locator must be well
  formed (`answers/<file>.json#<id>`, no path escapes), the file must be readable, the question must be answered in
  it, and `suppliedBy` and `recordedAt` must match the preserved answer. Evidence without a locator (promoted before
  this slice) is not checked.

## Known gaps

- Binding questions are generated without a catalog proposal, so they cannot be paired through bindings
  (`questionnaire bindings --proposal`), as in the pilot.
- An `application` selection with a subpath other than `.` is refused (no monorepo support).
- The drift count uses `git rev-list` on the source repository. When a repository is unreachable, the report
  says so instead of failing.
- The evidence auditor (#30) is slice 20.
