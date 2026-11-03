# Slice 17: deterministic catalog ids

Model-proposed catalogs used to carry whatever ids the model chose, so two runs over the same
evidence produced `…-app` once and a bare repository name the next. Promotion never removes
entities, so the second run would sit beside the first.

## Convention

| Entity | Id |
| --- | --- |
| Application with evidence from one repository | `<repositoryId>-app` |
| Deployable comprised by such an application | `<repositoryId>-deployable` |
| Term | its `name` in kebab-case, splitting camel case (`ProductPropalDTO` → `product-propal-dto`) |
| Relationship | `<sourceId>-<predicate>-<targetId>`, the predicate being the type without its entity types (`participates-in`) |
| Bounded context, use case, strategic capability | concepts: no pattern, see below |

Relationships added by `catalog link-config` keep their own `rel-<source>-feeds-<target>` ids.

## Tooling

- `catalog extract RESPONSE --output FILE [--current CATALOG]` applies the convention. With
  `--current`, a bounded context, use case or capability whose name matches a current entity
  (case and punctuation insensitive) adopts its id, and a relationship with the same type, source
  and target adopts the current relationship's id. Two entities that would end with the same id
  fail the extraction.
- `catalog propose-validate` rejects a proposal whose applications, deployables or terms depart
  from the convention, so a hand-edited or old proposal cannot slip through.

## Limits

Concepts that the model names differently ("Product Catalog Context" against three narrower
contexts) are different proposals, not different ids. The review lists the current ones as
"Kept, not in this proposal" for a human to decide.
