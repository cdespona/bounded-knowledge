# Slice 14: Kubernetes container environment variables

## Boundary

Detector `kubernetes-env` (version 1) runs on reviewed Kubernetes source selections next to
`kubernetes-images`. It reads `env` lists of supported workloads (Pod, Deployment,
StatefulSet, DaemonSet, ReplicaSet, Job, CronJob, and their Lists) and emits `env-variable`
observations. Real manifests keep many secrets as plain literals (a survey of one
production-sized manifests repository found hundreds of secret-named literals), so recording
is an allowlist, not a blocklist.

## Privacy policy

- A variable is recorded only when its name, or a `jdbc:`, `amqp:`, or SQS URL value,
  points to an infrastructure target: `jdbc`, `sqs`, `s3`, `kafka`, `kafka-topic`,
  `schema-registry`, or `amqp`. Every other variable (names, values, everything) is ignored.
- A recorded variable whose name looks sensitive (`password`, `secret`, `token`,
  `credential`, `private-key`, `api-key`, `access-key`) or whose value is credential-shaped
  (`://user:pass@`, `password=`) has disposition `withheld`: the observation stores no value,
  and evidence bundles replace the value lines with `<withheld>`.
- `valueFrom` entries are recorded as `reference` (`secretKeyRef`, `configMapKeyRef`,
  `fieldRef`, `resourceFieldRef`) without value; Secret manifests are not read.
- AWS account ids in literal values are masked as `<aws-account>`.

## Observation

`env-variable`: `name`, `container`, `workload` (`Kind/name`), `sourceSelectionId`,
`disposition` (`literal`, `withheld`, `reference`, `empty`), `value` (null exactly for
withheld and reference), `resource`, `reference`, `valueLine`. Its source lines span the
`name` line to the end of the value.

## Use in links

`catalog link-config` accepts the Kubernetes selection inventories and evidence bundles
beside the application ones.

- A manifests selection is paired with the application repository whose id equals the last
  folder of the selection subpath (a folder convention; the feed states it in
  `missingEvidence`).
- The profile of a manifest is the first directory named like a known environment
  (`dev`, `pro`, ...).
- A configuration key resolves from its property values, then environment variables
  override per profile. Relaxed binding names (`a.b-c` as `A_B_C`) match, and a property whose
  value is `${NAME}` or `${NAME:default}` is followed to the environment variable (or its
  default).
- `catalog propose-validate` now accepts several evidence bundles for the same repository
  and commit (one per selection).
