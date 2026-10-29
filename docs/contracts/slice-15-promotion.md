# Slice 15: Human-gated promotion into the canonical catalog

## Boundary

`catalog/landscape-catalog.json` is the single canonical catalog document (schema
`landscape-catalog.schema.json`), written only by `landscape catalog promote`. Promotion
records live in `catalog/promotions/`. Models, scanners, and `link-config` never write there.

## Flow

1. `catalog propose-validate` must pass: schema, evidence bound to bundles, answers attributed,
   testimony never alone for `confirmed`.
2. `catalog review PROPOSAL --current catalog/landscape-catalog.json --draft DRAFT` renders the
   diff and every assertion. Its last line states the digest of the text above it.
3. A human reads the review and runs `catalog approve ... --reviewed DIGEST --by NAME --at TS`.
   The command refuses unless the digest matches the review of this proposal against the
   current catalog, so an approval always names the exact text that was read.
4. `catalog promote ... --approval FILE --catalog ... --promotions ...` revalidates and
   verifies that the approval matches the proposal digest, the review digest, and exactly the
   added and changed assertions. Any mismatch aborts with nothing written. `--dry-run` shows
   the effect without writing.

## Semantics

- Entities and relationships are added, or replaced by id when they differ. A replaced
  assertion keeps whatever evidence and counterevidence the approved proposal carries; the
  prior version remains in git history and the record names the digest before and after.
- The writer never removes anything. Entities and open questions the proposal does not mention
  stay and appear under "Kept, not in this proposal"; removal needs a later explicit contract.
- Open questions are unioned.
- The catalog is serialized with sorted keys, two-space indentation and one trailing newline.
  The merged document is validated before the write; the catalog is replaced atomically and
  the promotion record is written first, never overwriting an existing record.
- Promoting an unchanged proposal is a no-op.

## Promotion record

`catalog/promotions/<UTC time>-<proposal digest prefix>.json` holds the proposal and review
digests, approver and time, the accepted and retained assertion ids, the catalog digests
before and after, the questionnaire draft id, and the evidence bundle ids. Interview answers
and evidence bundles stay in `work/` and are not part of the catalog.
