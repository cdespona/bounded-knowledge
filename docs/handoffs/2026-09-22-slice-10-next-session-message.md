# Next Agent Session Message: Slice 10 Direction Discussion

Copy the message below into a new agent session.

---

Continue the Bounded Knowledge initiative in:

`/Users/cdespona/personal/bounded-knowledge`

Start from the latest pushed `main` commit containing the completed Slice 9 Kubernetes
YAML workload-image inventory and the Slice 10 direction-selection handoff. Resolve and
report the exact SHA of `HEAD`, `main`, and local `origin/main` before editing; do not
assume they match and do not fetch unless separately authorized.

Before acting, completely read:

- `AGENTS.md`
- `README.md`
- `docs/handoffs/2026-09-22-slice-10-direction-selection-handoff.md`
- `docs/initiative/landscape-plan.md`
- `docs/initiative/deferred-capabilities.md`
- every additional contract, schema, implementation, fixture, and test identified as
  required reading by the handoff

Your first objective is a discussion-backed direction decision, not automatic contract
writing or implementation. Begin with parallel read-only repository research comparing:

1. OpenAPI and AsyncAPI YAML parity with the existing Slice 6 JSON inventory;
2. one bounded Terraform literal inventory over an explicit Terraform source selection;
3. stopping detector expansion to prepare an explicitly authorized manual private pilot;
4. neither, or a user-proposed alternative that receives the same research gates.

Do not inspect private repositories or infer real-landscape facts during this research.
Use canonical repository evidence to establish existing capabilities, candidate
boundaries, structural coverage, parser/dependency needs, provenance, safety, value, and
known gaps.

After research, stop and present the evidence-backed comparison table required by the
handoff. Then grill the user one question at a time. For every question, provide your
recommended answer and rationale, distinguish repository evidence from assumptions, and
ask only for a choice or real-world context the repository cannot establish. Record each
explicit answer.

Obtain an explicit user choice before selecting the Slice 10 direction, freezing a
contract, changing roadmap or deferred statuses, accessing private repositories,
implementing anything, or selecting another detector. Obtain separate explicit
authorization for contract work, implementation, private-repository access, commit, and
push. Silence or lack of objection is not approval.

If implementation is eventually authorized, follow the handoff's contract-first →
implementation → independent-review sequence. Parallelize only explicitly non-overlapping
files, integrate shared surfaces centrally, run portable schema parity for persisted
contracts, run focused and complete deterministic tests, and resolve every independent
review finding.

Important boundaries:

- Preserve the user-owned `sources.json` modification. Do not stage, revert, overwrite,
  print, summarize, or include it in patches.
- Do not inspect ignored `work/`, `graphify-out/`, private repositories, or private
  evidence without exact authorization.
- Do not execute builds, wrappers, hooks, generators, plugins, Docker, BuildKit,
  Compose, Kubernetes, Helm, Kustomize, Terraform, or application scripts.
- Do not read Terraform state, plans, `.terraform/`, variable-value files, `.env`,
  kubeconfigs, private keys, certificates, secret values, generated code, vendor trees,
  dependency caches, or build output.
- Do not access registries, clusters, URLs, module sources, remote references, or network
  services during research.
- Do not invoke Copilot or another external model for repository analysis.
- Do not add a hand-written partial YAML or HCL parser.
- Do not infer runtime deployment, infrastructure existence, ownership, security, cost,
  provenance, usage, cross-repository relationships, or business meaning.
- Do not implement Conductor, canonical promotion, the private pilot, or the website
  unless that exact direction is later authorized.
- Do not commit or push without explicit authorization.

At the discussion stop, report the exact starting SHA and ref relationship, comparison,
recommendation, all grill questions and explicit answers, selected direction or absence
of one, authorizations actually granted, changed files, validation, known gaps, deferred
register changes if authorized, exact dirty state, and pending permissions.

