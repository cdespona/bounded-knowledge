# How should an LLM skill reach the extracted data safely?

Research for issue #17. Researched 2026-11-16 against Copilot CLI 1.0.107 and the
Claude Code and MCP documentation as published on that date. This is input to a later
human decision. It is not the decision.

## Answer

Build option (a), a bounded, deterministic `landscape query` command. Do not have
agents call it directly as a shell tool. Run it in front of the model, the same way
`landscape stage prepare` already works:

1. The `landscape-query` skill, or a workflow step, runs `landscape query` with a small
   fixed vocabulary (entity by id, relationships of an entity, claims by status,
   evidence for a claim, open questions). The command writes a size-capped JSON slice
   into a fresh stage directory. Every record keeps its `status`, `confidence`,
   `evidence` and `counterevidence` fields exactly as they appear in the validated
   source.
2. The model reads only that staged slice. For Copilot it runs with the existing
   `--available-tools=view` and no other tools. For Claude Code it runs in a subagent
   whose `tools` allow-list contains only `Read`. Its answer is checked
   deterministically: every cited claim id and evidence locator must exist in the slice.

Option (b), direct JSON reads, is fine as a short-term fallback while the catalog is
small, but it does not scale on tokens and its path scoping depends on staging.
Option (c), an MCP server, is the better shape for interactive use later, once the query
vocabulary is stable. It adds a long-running process, client configuration, and a trust
surface (tool annotations are untrusted hints). The repository already lists
"unbounded MCP access" as a permanent boundary (`S4-B01`). A later MCP server should be
a thin wrapper over the same query module, so option (a) is not wasted work.

The deciding observation: neither client gives a reliable read-only, path-bounded *shell*
permission. Copilot's shell rules match on the command stem. Claude Code's docs say Bash
argument patterns are "fragile" and that Read deny rules do not reach "arbitrary
subprocesses". So the safe design is to keep shell out of the model's toolset. Run the
deterministic query before the model is invoked, and give the model file-read access
only.

## What the repo already requires

- `AGENTS.md`: model-backed analysis stays read-only. No `--allow-all` or
  `--allow-all-paths`. Claims are classified `confirmed`, `inferred` or `unknown`, and
  cite repository, full commit SHA, path and line range. Promotion needs a deterministic
  validator and a human gate.
- `docs/initiative/landscape-plan.md`, "Agent skill strategy": skills "should consume
  validated catalog data or bounded evidence through documented commands". Milestone 6
  asks for "a bounded query command used by both the website and `landscape-query`
  skill." The "Token controls" section asks for evidence selected by question, explicit
  per-agent input and output limits, and reuse of validated claims.
- `docs/initiative/deferred-capabilities.md`: `S1-D10` (bounded query layer) and
  `S1-D11` (skill suite) are deferred until after the pilot. `S4-B01` makes write, shell,
  broad-path or unbounded MCP access for the cartographer a permanent boundary.
- `tools/landscape_core/stages.py`: today Copilot runs as
  `copilot -C <stage_dir> --agent <name> --available-tools=view --disable-builtin-mcps
  --disallow-temp-dir --no-ask-user --silent --prompt ...`. The stage directory holds
  only `inputs/`, a copied `.github/agents/<agent>.agent.md`, `prompt.txt` and
  `inputs.sha256`. So the path boundary comes from *staging* (the working directory
  contains only what was selected), not from a path rule.
- `schemas/landscape-catalog.schema.json`: every entity and relationship carries an
  `assessment` with `status`, `confidence`, `evidence[]`, `counterevidence[]` and,
  depending on status, `missingEvidence`. `repositoryEvidence` has `repositoryId`,
  a 40–64 hex `commit`, `path`, optional `lines` and `observationId`. A query result that
  passes these objects through unchanged keeps the evidence classification and the
  traceable citations without any extra work.

## Comparison

| Criterion | (a) `landscape query` staged before the model | (b) Agent reads JSON files directly | (c) MCP server with read-only tools |
| --- | --- | --- | --- |
| Model toolset | File read only (`view` / `Read`) | File read only | MCP tools; built-in file and shell tools can be withheld |
| Path boundary | Stage directory holds only the slice | Needs staging or path rules; Claude `Read` deny rules are "best-effort" for Grep/Glob | Server decides which files exist; client path rules not needed |
| Shell needed by model | No | No | No |
| Read-only guarantee | Structural: no write or shell tools exist | Structural, same as (a) | Depends on server code; `readOnlyHint` is an untrusted hint per the MCP spec |
| Evidence classification and citations | Preserved by passing schema objects through; checkable | Preserved in the files, but the model may skim and lose them | Preserved if tools return schema objects (`outputSchema`/`structuredContent`) |
| Deterministic, stable output | Yes, same as other `landscape` commands | N/A (no query step) | Yes if built on the same module |
| Token cost as catalog grows | Bounded by the slice cap; grows with the question, not the catalog | Grows with catalog size; whole files are loaded | Bounded per call, but multi-call loops can add up; tool schemas sit in context |
| Interactive follow-up questions | Each new question needs a new slice (re-run) | Free within the files present | Natural: model calls tools as needed |
| New moving parts | One CLI subcommand plus tests | None | Server process, client config in two tools, a protocol dependency |
| Fit with `S4-B01` and current stage pattern | Direct fit | Fits (it is today's pattern for evidence bundles) | Allowed only if "bounded"; needs a decision |
| Works in Copilot CLI and Claude Code | Yes (both just read files) | Yes | Yes, but configured differently in each |

## How each client packages the capability

### GitHub Copilot CLI

- **Custom agents.** Agents are stored in `.github/agents/*.agent.md` (repository),
  `~/.copilot/agents` (user), or the org `.github` repo, and are run with
  `copilot --agent=<name>`. A user-level agent overrides a repository-level agent with
  the same filename.
  [Invoking custom agents](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/invoke-custom-agents)
  That precedence is one reason `stages.py` copies the agent into a fresh directory and
  runs with `-C`.
- **Agent frontmatter.** `tools` takes names or aliases. `read` maps to `view`,
  `execute`/`shell` to the shell, `search` to search, `edit` to the edit tools.
  `tools: []` disables all tools, and omitting `tools` enables all of them. MCP tools are
  named `server-name/tool-name` or `server-name/*`. `mcp-servers` adds servers.
  `disable-model-invocation` replaces the retired `infer`. The page does not mention
  path restrictions.
  [Custom agents configuration](https://docs.github.com/en/copilot/reference/custom-agents-configuration)
- **Tool filtering versus approval.** `--available-tools` "disables all tools other than
  those you specify", and tools outside it cannot be used even with `--allow-tool`.
  `--allow-tool`/`--deny-tool` only control prompts, and "deny rules always take
  precedence over allow rules, even when `--allow-all` is set". MCP tools are matched as
  `SERVER(tool)`. Shell is matched as `shell(cmd)` or `shell(cmd:*)`.
  [Allowing and denying tool use](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/allowing-tools)
- **Path permissions** (from `copilot help permissions`, v1.0.107): "By default, file
  access is restricted to paths within the current working directory and its
  subdirectories, plus the system temporary directory." `--disallow-temp-dir` removes
  the temporary directory. `--allow-all-paths` disables the check. `--add-dir` grants a
  directory *and* "load[s] its .github/skills and .github/agents as trusted
  configuration", which matters if anyone is tempted to `--add-dir` a catalog folder.
  `read(path)` and `write(path)` rules take gitignore-style globs. "A rule with a path
  does not apply to shell commands." Shell wildcard matching "is performed on the stem
  of the command".
- **Skills.** Skills live in `.github/skills`, `.claude/skills` or `.agents/skills`
  (project) and `~/.copilot/skills` (personal), each with a `SKILL.md` containing
  `name`, `description`, and optional `license` and `allowed-tools`. `allowed-tools`
  *pre-approves* tools. It does not restrict them, and the docs warn that pre-approving
  `shell` lets prompt injection run arbitrary commands.
  [Creating skills](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-skills)
- **MCP.** `--additional-mcp-config` adds servers for one session.
  `--disable-builtin-mcps` turns off the built-in GitHub servers. Both are in
  `copilot --help` for v1.0.107.

What this means here: a Copilot query agent can stay exactly as hardened as the current
stage agents (`--available-tools=view`, cwd = stage directory) if the slice is staged
first. Letting the agent call `landscape query` would mean adding a shell tool to
`--available-tools`. The only scoping then is a command-stem pattern, which conflicts
with `S4-B01`. A future MCP route would be `--available-tools=<server>(...)` plus
`--additional-mcp-config`, with `view` dropped.

### Claude Code

- **Skills.** Skills live in `.claude/skills/<name>/SKILL.md` (project),
  `~/.claude/skills` (personal), plugins, and `--add-dir` directories. The frontmatter
  includes `description`, `allowed-tools`, `disallowed-tools`, `disable-model-invocation`,
  `context: fork` and `agent`. `allowed-tools` "grants permission ... It does not
  restrict which tools are available". Use `disallowed-tools` to remove tools while the
  skill runs. Only descriptions load at startup, and the body loads on use and then
  "stays in context across turns". ``!`cmd` `` runs a command *before* the skill is sent
  and injects its output. That gives a ready-made "query first, model second" pattern:
  ``!`./tools/landscape query ...` ``. Admins can disable it with
  `disableSkillShellExecution`.
  [Skills](https://code.claude.com/docs/en/skills)
- **Subagents.** `.claude/agents/*.md` with `tools` as an allow-list (omitted means
  inherit all), `disallowedTools`, `mcpServers` and `permissionMode`. A skill with
  `context: fork` runs inside such an agent, so `tools: Read` gives the same "file-read
  only" posture as Copilot's `--available-tools=view`.
  [Subagents](https://code.claude.com/docs/en/sub-agents)
- **Permissions.** Rules are evaluated "deny, then ask, then allow". A bare tool name in
  deny removes the tool from context. `Read(path)`/`Edit(path)` use gitignore syntax
  (`//abs`, `~/`, `/relative-to-settings`, `relative`). Read deny rules apply to built-in
  file tools and recognised Bash file commands, but "don't apply to ... arbitrary
  subprocesses that read or write files indirectly, like a Python or Node script". Bash
  argument patterns "are fragile". By default Claude can read the launch directory plus
  `--add-dir` / `additionalDirectories`, and `blockReadsOutsideWorkingDirectories`
  hardens that. MCP rules are `mcp__<server>__<tool>`.
  [Permissions](https://code.claude.com/docs/en/permissions)

What this means here: a Claude Code `landscape-query` skill can (1) inject a
`landscape query` result with ``!`...` ``, or (2) `context: fork` into a `Read`-only
subagent pointed at a staged slice. Allowing `Bash(./tools/landscape query *)` would work
in practice, but the docs themselves describe such argument scoping as fragile, and a
Python subprocess is outside Read deny rules. The CLI itself would then be the only
boundary.

### MCP (option c)

- The spec says clients "MUST consider tool annotations to be untrusted unless they come
  from trusted servers", and that servers "MUST validate all tool inputs, implement
  proper access controls, rate limit ... sanitize tool outputs". Tools may declare an
  `outputSchema` and return `structuredContent`, which could carry the catalog's
  `assessment` objects unchanged.
  [MCP tools specification, 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- So "read-only" in MCP means "the server we wrote only reads". That is the same trust
  basis as option (a), plus a protocol layer and a running process. The benefit is that
  the model can make multiple narrow calls during an interactive conversation, instead
  of getting one pre-selected slice.

## Token cost as the catalog grows

- **(b)** loads whole files. Cost grows roughly linearly with the number of entities,
  relationships and evidence entries in the promoted catalog, inventories and bundles.
  It breaks the "Token controls" rule of selecting evidence by question.
- **(a)** cost depends on the question's slice, not on the catalog size. A hard cap
  (records and bytes) set in the command, plus a visible `truncated: true` marker,
  reports overflow as a gap rather than silently widening scope.
- **(c)** per-call results are bounded, but every tool definition sits in context, and an
  exploratory loop can make many calls. Claude Code documents that bare-name deny removes
  a tool from context. The overall cost is not quantified in the sources consulted here.
- Skill bodies cost tokens only on use in Claude Code (descriptions are loaded at
  startup). The Copilot skills page does not say how loading works.

## Suggested shape if (a) is chosen (for the later decision)

- `landscape query <verb> --catalog <path> [--limit N] [--max-bytes B]`, standard
  library only. Output is stable JSON with `sort_keys`, echoes the query and the catalog
  file's SHA-256, and passes `assessment` objects through unchanged.
- Verbs drawn from the planned views: `entity`, `relationships`, `claims --status`,
  `evidence --claim`, `open-questions`, `gaps`. Fixed verbs only. No free-form
  expression language.
- Inputs restricted to promoted, validated artifacts (or explicitly named candidate or
  bundle files that pass their validators), matching `S4-B02`.
- A checker, in the style of `stage check`, rejects an answer that cites ids or evidence
  locators absent from the slice, or that drops a claim's `status`.
- The same module later backs the static website (Milestone 6) and, if wanted, an MCP
  wrapper.

## Open uncertainties

- Whether Copilot `shell(...)` patterns can usefully match a repository-relative
  executable such as `./tools/landscape query` was not tested. The help text only says
  matching works on "the stem of the command". This only matters if agents are ever
  given shell.
- The full `--add-dir` help text was truncated in this capture. The part quoted (loads
  `.github/skills` and `.github/agents` as trusted configuration) was confirmed, the
  remainder was not.
- How Copilot CLI loads skill bodies (eagerly or lazily), and so their token cost, is
  not documented on the pages consulted.
- Copilot custom-agent `mcp-servers` frontmatter is documented for the cloud agent. Its
  behaviour in the local CLI with `--available-tools` was not verified.
- MCP token overhead (tool-definition size, call loops) was not measured. A comparison
  needs a prototype once a real query vocabulary exists, which `S1-D10` defers until
  after the pilot.
- The right size cap for a slice is unknown until pilot questions are collected.
