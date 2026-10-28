# Slice 12 Application Configuration Inventory Contract

## Purpose

Slice 12 records the keys and values of application configuration files so that runtime
bindings (topics, queues, database endpoints) can be read from files instead of guessed.
It reports static file content only. It does not claim that a value is the one used at
runtime, that a resource exists, or that two repositories share a resource.

## Candidate boundary

Detector `config-properties`, version `1`, reads safe regular files under
`src/main/resources/` ending in `.properties`, `.yaml`, or `.yml`, at any depth. The
generic file, every profile file, and every profile override are all recorded; none is
preferred. Existing path, symlink, sensitive-name, size, and UTF-8 rules apply first.
Test resources are out of scope.

## Observation vocabulary

`schemas/config-observation.schema.json` defines the shape.

- `config-property`: `key`, `profile`, `form`, `line`, `disposition`, `value`, `resource`.
  The source carries only the path; the evidence selection reads the whole file as one
  range.
  - `profile` comes from a `%profile.` key prefix (Quarkus), a known profile suffix on the
    file name (`application-pro`, `jdbc-pro`), or is `null` for generic values.
  - `disposition` is `literal`, `placeholder` (the value contains `${...}`), `empty`, or
    `withheld`. A withheld value is stored as `null` and is used for keys naming a
    password, secret, token, credential, or key.
  - `resource` is a lexical hint (`jdbc`, `sqs`, `kafka`, `kafka-topic`,
    `schema-registry`, `amqp`) or `null`. It is never a semantic claim.
- `config-gap`: constructs that are not interpreted (line continuations, profile
  expressions such as `%dev,test.`, YAML block scalars, anchors, flow collections,
  non-scalar list items, tab indentation, further YAML documents).

## Privacy

AWS account identifiers in `amazonaws.com/<id>/`, `localhost:<port>/<id>/`, ARN, and
`account-id=` forms are replaced by `<aws-account>` in observations and in the content of
selected evidence. Withheld values are replaced by `<withheld>` in selected evidence.
Evidence content for configuration files is masked line for line, so line numbers stay
valid.

## Known gaps

Environment-specific values kept outside the repository (secrets, deployment config) are
not visible and must come from the questionnaire. Spring multi-document YAML is reported
as a gap after its first document.
