# Next Agent Session Message: Slice 8

Copy the message below into the next agent session.

---

Continue the Bounded Knowledge initiative in:

`/Users/cdespona/personal/bounded-knowledge`

Start from the latest pushed `main` commit containing the Slice 7 Kafka literal inventory
and the Slice 8 handoff. Resolve and report the exact SHA before editing.

Your objective is to complete Slice 8: a narrow, deterministic container-image literal
inventory.

Before acting, read `AGENTS.md`, `README.md`, and the complete handoff:

`docs/handoffs/2026-09-18-slice-8-container-images-handoff.md`

Follow the contract-first → implementation → independent-review sequence described in
the handoff. Use parallel agents only for genuinely isolated, non-overlapping work:
begin with parallel read-only contract research, false-positive analysis, and
fixture/test-matrix design; integrate one contract centrally before implementation; then
parallelize only files with explicit ownership. Run an independent read-only review after
integration and resolve every finding before reporting completion.

Important boundaries:

- Preserve the existing user-owned `sources.json` modification. Do not stage, revert,
  overwrite, print, summarize, or include it in patches.
- Do not inspect or use ignored `work/` artifacts or `graphify-out/` as implementation
  evidence; some artifacts may contain private-repository-derived information.
- Do not inspect private repositories.
- Do not run source-repository builds, wrappers, hooks, generators, plugins, Docker,
  BuildKit, Compose, Kubernetes, Helm, Kustomize, or application scripts.
- Do not access registries, clusters, URLs, or network services.
- Do not invoke Copilot or another external model for repository analysis.
- Use Python 3.9+ standard library only for repository implementation.
- Emit deterministic observations and explicit gaps; never infer runtime deployment,
  image existence, security, ownership, cross-repository relationships, or business
  meaning.
- Do not add a partial YAML parser merely to widen the slice.
- Do not implement Conductor, canonical promotion, the private pilot, or the website.
- Do not commit or push without explicit authorization.

The default contract direction is Dockerfile `FROM` literal inventory first. Add bounded
deployment-file positives only if contract research proves a safe candidate and parsing
boundary; otherwise defer them explicitly.

Run portable schema parity checks, the complete deterministic suite, and Git diff/status
checks. At completion report changed files, validation, known gaps, independent-review
findings, exact dirty state, and whether commit/push authorization remains pending.
