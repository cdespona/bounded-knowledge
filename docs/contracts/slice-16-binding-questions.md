# Slice 16: Gap-derived binding questions and structured answers

## Boundary

Some targets stay unknown after code, configuration, and manifests have been read: a bucket name
passed through dependency injection, a queue URL that only exists in a secret. This slice turns
those exact gaps into questions without a model, and lets the answer come back as data that
`link-config` can use. Nothing here becomes canonical on its own.

## Generating the questions

```text
./tools/landscape questionnaire bindings --inventory INV... --evidence EV... \
    [--topology TOPOLOGY --proposal PROPOSAL] --respondent-id ID --respondent-role ROLE --output DRAFT
```

- Candidates are `io-point` observations of Kafka, SQS, S3, or AMQP producers and consumers whose
  target does not resolve through literals, configuration keys per profile, or the paired Kubernetes
  environment (the same resolution `link-config` uses; see slices 13 and 14).
- A parameter whose callers were resolved asks nothing. Observations that no evidence bundle selected
  are counted and skipped, because a question needs a pinned scope.
- One question per repository, technology, role, and reference. The wording, ids, and
  `whyCodeCannotSettle` come from templates, so the draft is identical for identical inputs. The
  explanation says where the target is read and, when the manifests pass it by `secretKeyRef` or hold
  it withheld, says so.
- The draft is a normal unanswered questionnaire draft for one respondent; it can be assembled with
  model-written batches or used alone.

## Contract additions (questionnaire draft)

- Link type `observation`: `{type, repository, commit, evidenceBundleId, observationId}`. It points at
  an observation of the pinned bundle instead of a claim or a bundle gap.
- Question field `expects: "target-values"` (human-only questions only): the answer carries values.
- Answer field `values`: sorted, unique `{profile, value}` entries, `profile` null for "the same
  everywhere" or a lowercase environment name. Required for an answered `expects` question and
  forbidden otherwise. A value is one trimmed line, has AWS account ids masked as `<aws-account>`,
  and cannot look like a credential (`user:password@`, `password=`); recording rejects such a value.

## Recording the answers

`answers-template` lists `values` for these questions. In the Markdown form, the respondent fills
`- environment: value` lines under `Values:` (`default` when identical everywhere). `record`
normalizes profiles, masks account ids, sorts the values, and, when the free text is empty, writes
`Target values: ...` as the exact text. `unknown` and `deferred` answers carry no values and support
nothing.

## Using the answers

`catalog link-config ... --answers DRAFT-ANSWERED` validates the draft and resolves every answered
observation to its per-environment target. A feed built on an answer cites the interview answer as
curated evidence next to the repository evidence, states in `missingEvidence` that the target comes
from an interview answer, and stays `inferred`. `catalog propose-validate` already checks that each
cited answer exists and matches its answerer and time.
