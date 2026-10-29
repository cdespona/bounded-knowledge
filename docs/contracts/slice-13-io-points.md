# Slice 13: Messaging and persistence I/O points

## Boundary

Detector `io-points` (version 2) reads Java and Kotlin files under `src/main/java` and
`src/main/kotlin` and reports where code produces to or consumes from a queue, topic, or
channel and where it reads, writes, or maps persistent storage. It reports the lexical
target and how it was named. It never decides what the target means. Test sources, comments,
and generated or excluded paths are skipped.

## Observations

`io-point` carries `technology`, `role`, `construct`, and a `binding`; its source always has
the path and line range. `io-gap` (`code`, `detail`) reports a recognised construct whose
target cannot be read, for example a `@KafkaListener` without `topics`.

| Technology | Roles | Recognised constructs |
| --- | --- | --- |
| `kafka` | producer, consumer | `@KafkaListener`, `KafkaTemplate.send/sendDefault`, `ProducerRecord`, `KafkaConsumer.subscribe`, Kafka Streams `stream/table/globalTable` and `to/through` |
| `messaging` | producer, consumer | `@Incoming`, `@Outgoing`, `@Channel` emitters, `StreamBridge.send` (resolved through configuration) |
| `sqs` | producer, consumer | AWS SDK v1/v2 send, receive, and delete requests with a queue URL, `@SqsListener`, `SqsTemplate` |
| `s3` | producer, consumer | AWS SDK v1/v2 object requests and lambda-style calls with a bucket (`bucket`, `sourceBucket` as consumer, `destinationBucket` as producer), Spring Cloud AWS `S3Template`; puts, deletes, and uploads are producers, gets, heads, and lists are consumers |
| `amqp` | producer, consumer | `@RabbitListener`, `RabbitTemplate` sends |
| `persistence` | declaration, reader, writer, mapped | `@Repository`, Spring Data and Panache repositories, `@Entity/@Table/@Document`, SQL and `@Query` statements, uppercase table literals passed to `super.`/`this.` calls inside repository classes |

`binding.form` says how the target was named: `literal`, `config-key` (a `${key}`
placeholder, `@Value`, or `@ConfigProperty`, directly or through a parameter, field alias, or
a record built from such parameters), `channel`, `placeholder` (SQL template such as
`$TABLE_NAME$`), `type` (entity or repository type), or `unresolved` (the expression text).
`binding.via` records the resolution path. Besides `@Value`, `@ConfigProperty`, constants, and
records, a name resolves through: variables assigned from `System.getenv("K")`, `getProperty("K")`,
or `ConfigProvider...getValue("K")` (also as a direct argument); helper methods that read an
environment variable named by their argument; `@ConfigurationProperties(prefix)` classes read
through accessors (`prefix.field`, matched in camelCase or kebab-case); and parameterless
provider methods that return such a read, matched to an injection point by name (the `via` says
so). Fields assigned from a constructor parameter are followed to the callers of that
constructor. Call sites whose argument stays unresolved add no point. Methods that forward a parameter to a messaging
call are followed by name when the method name is unique in the repository.

Literals pass through the AWS account mask used for configuration files, and every selected
source line is masked the same way in evidence bundles.

## Evidence

Files with I/O points are ranked after Kafka literals and before configuration files. They
stay whole-file selections: I/O observations do not narrow a file to their own lines. The
catalog cites the exact I/O lines inside the selected range.

## Producer and consumer links

`catalog link-config` now also rebuilds `application-feeds-application` (source is upstream).

- Points resolve to a comparable target per profile: configuration values are looked up by
  key, a messaging channel is resolved through `mp.messaging.<incoming|outgoing>.<channel>.*`
  with its connector, and an incoming Kafka channel without a topic uses the channel name.
- Producers (or writers) in one repository pair with consumers (or readers) in another when
  they name the same topic, SQS queue name, S3 bucket, AMQP destination, or table in a profile other
  than `local` and `test`. The profile `default` applies where a profile has no override.
- One relationship per ordered application pair lists every matched target, is `inferred`
  with `medium` confidence, and states in `missingEvidence` that the same cluster, account,
  or database instance is not established. SQS targets compare queue names only.
- Tables that two applications touch without a producer/consumer split become shared-resource
  relationships.
- Targets that stay unresolved (for example an environment variable that no manifest or property
  defines) stay visible as `unresolved` points; they produce no relationship and are a natural subject for
  questionnaire questions.
