# Glossary

Terms the observatory uses with a specific meaning. Definitions describe the domain,
not the implementation.

## Run

One pass of the observatory over a set of applications that you choose. The
applications are analyzed together: per-repository stages run for each of them, and
joint stages see the whole set. A run is pinned to the commits found when it starts.
Everything it produces stays in one place until it is promoted.

## Survey

The first part of a run. It discovers the applications, selects their evidence, has
the model describe each one, and ends with a questionnaire for you. If any application
fails to produce a description, the survey ends without a questionnaire. A partial set
would hide questions about the missing application.

## Questionnaire

The questions that a survey leaves for a human: anything the evidence cannot settle,
such as whether two queues seen in different applications are the same one. Answers
are confirmed evidence and are kept with the catalog after promotion.

## Synthesis

The second part of a run. It combines the survey's descriptions with your answers,
always at the commits pinned by the survey, into a proposed catalog change. It ends
with a review that shows the change against the current catalog, and a digest. The
review warns when an application has moved past its pinned commit.

## Digest

A fingerprint of one reviewed catalog change. Approving a digest approves exactly that
change and nothing else.

## Promotion

The third part of a run: writing an approved change into the catalog. It is the only
way the catalog is written, and it refuses any change whose digest you did not approve.

## Human gate

A point where a run stops until a person acts. There are two: answering the
questionnaire and approving the digest. Both come after the model's semantic analysis.
Deterministic steps never wait for approval. Rejecting a digest writes nothing; you
correct your answers and repeat the synthesis, or start a new survey.
