# Slice 18: stage commands and Conductor workflows

The manual pilot ran its model steps through ad hoc shell scripts that staged inputs, pasted prompts and
looped on validation. This slice moves that into tested commands and lets Conductor only sequence them
(milestone 8 of the plan).

## `landscape stage`

| Command | Does |
| --- | --- |
| `stage prepare STAGE --output-dir DIR --evidence … [--candidate …] [--answers …] [--analyzed-at …]` | Validates the inputs, copies them into `DIR/inputs` under repository-based names, copies the one agent definition, renders `DIR/prompt.txt` from `workflows/prompts/`, and records `inputs.sha256`. Refuses a non-empty directory. |
| `stage run DIR --output FILE [--timeout S]` | Runs the staged agent once and stores stdout and stderr. Exit code 1 if Copilot failed. |
| `stage check STAGE --response FILE --evidence … [--answers …] [--current CATALOG] --output FILE` | Extracts and validates a captured response with the same checks as `candidate validate`, `questionnaire assemble` and `catalog extract` plus `propose-validate`. Writes the document only when valid. |

Stages are `cartographer`, `curator-business` (the business-semantics batch; runtime bindings come from
`questionnaire bindings`) and `synthesis`. The questionnaire shape the curator must follow is generated from the
evidence bundles, not pasted into the prompt by hand.

Copilot is always started as `copilot -C DIR --agent NAME --available-tools=view --disable-builtin-mcps
--disallow-temp-dir --no-ask-user --silent --prompt …`, with `DIR` holding only the staged inputs.

## Why Conductor does not call Copilot itself

Conductor's Copilot provider answers every tool permission request with approval and relies on the workflow's `tools:`
allowlist. That is equivalent to broad path permissions for `view`, which `AGENTS.md` forbids. Workflows therefore use
only `script` steps that run `./tools/landscape`, and `stage run` keeps the hardened flags in tested code.
`tools/tests/test_workflows.py` rejects agent steps, providers and broad permission flags in `workflows/`.

## Workflows

Pinned release: Conductor v0.1.44 (`microsoft/conductor`, commit `65e63232`). Run from the repository root.

- `workflows/cartographer.yaml`: prepare, run, check, with at most three attempts. Verified against real evidence.
- `workflows/promotion.yaml`: validate, review, human gate, approve, promote. The first gate option is reject, so
  `--skip-gates` never approves. The approver types the review digest; it is passed to `catalog approve --reviewed`.
  Verified on a throwaway catalog: the reject path writes nothing; the approve path promotes.

Limits:

- The attempt cap (`max_iterations`) is the only cost limit, because the model runs as a subprocess and Conductor
  does not meter it.
- Evidence arguments of `promotion.yaml` are space separated and must not contain spaces.
- Not orchestrated yet: discovery and evidence selection, the curator and synthesis stages, `link-config`, the
  evidence auditor and the candidate reviser.
