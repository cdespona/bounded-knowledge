# Next Agent Session Message: Slice 9

Copy the message below into the next agent session.

---

Continue the Bounded Knowledge initiative in:

`/Users/cdespona/personal/bounded-knowledge`

Start from the latest pushed `main` commit containing the completed Slice 8 Dockerfile
container-image inventory and the Slice 9 handoff. Resolve and report the exact SHA before
editing.

Before acting, read `AGENTS.md`, `README.md`, the complete handoff, and the deferred
capabilities register:

- `docs/handoffs/2026-09-21-slice-9-deployment-images-handoff.md`
- `docs/initiative/deferred-capabilities.md`

Your first objective is contract research, not automatic implementation: determine
whether one narrow deployment-image serialization—Kubernetes JSON or a precisely bounded
Docker Compose serialization—has both a safe candidate-file boundary and complete
structural coverage. Research alone does not authorize implementation or final deferral.

After research, stop and present an evidence-backed comparison of Kubernetes JSON,
Compose, and neither. Then grill the user one question at a time, providing your
recommended answer and rationale for each question. Answer questions from repository
evidence yourself when possible; ask only for choices or real-world context the
repository cannot establish. Obtain an explicit user choice before freezing a contract,
changing deferred statuses, implementing anything, or selecting the next roadmap
detector. Silence or lack of objection is not approval.

Follow the contract-first → implementation → independent-review sequence in the handoff.
Begin with parallel read-only Kubernetes JSON research, Compose boundary research, and
adversarial completeness/false-positive analysis. Integrate the comparison centrally,
run the mandatory grill, and stop for explicit approval. Only after approval may you
record the decision and, if authorized, parallelize explicitly non-overlapping
implementation files. Run an independent read-only review after integration and resolve
every finding.

Important boundaries:

- Preserve the user-owned `sources.json` modification. Do not stage, revert, overwrite,
  print, summarize, or include it in patches.
- Do not inspect ignored `work/`, `graphify-out/`, private repositories, or private
  evidence.
- Do not execute builds, wrappers, hooks, generators, plugins, Docker, BuildKit,
  Compose, Kubernetes, Helm, Kustomize, Terraform, or application scripts.
- Do not access registries, clusters, URLs, remote references, or network services.
- Do not invoke Copilot or another external model for repository analysis.
- Do not add a hand-written partial YAML parser.
- Do not infer runtime deployment, image existence, security, ownership, provenance,
  cross-repository relationships, or business meaning.
- Do not implement Conductor, canonical promotion, the private pilot, or the website.
- Do not commit or push without explicit authorization.

Run portable schema parity checks if a persisted contract is added, the complete
deterministic suite, and Git diff/status checks. Maintain
`docs/initiative/deferred-capabilities.md`. At completion report the exact starting SHA,
scope decision, changed files, validation, known gaps, register changes, independent
review findings, the grill questions and explicit user decision, exact dirty state, and
pending commit/push authorization.
