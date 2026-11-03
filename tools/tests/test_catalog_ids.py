"""Tests for deterministic catalog ids."""

import copy
from pathlib import Path
import sys
import unittest

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))

from landscape_core.catalog_ids import convention_errors, normalize_ids, slug  # noqa: E402

AT = "2026-10-29T10:00:00Z"


def assessment(repository=None):
    evidence = [{"kind": "repository", "repositoryId": repository, "commit": "a" * 40, "path": "x"}] if repository else []
    return {"status": "inferred", "confidence": "medium", "analyzedAt": AT, "evidence": evidence, "counterevidence": []}


def ref(entity_type, entity_id):
    return {"entityType": entity_type, "entityId": entity_id}


def run(application, deployable, context, use_case, term_name, term_id, relationship_prefix):
    """One synthetic model run; the ids differ between runs, the meaning does not."""
    relationships = [
        ("application-comprises-deployable", ref("application", application), ref("deployable", deployable)),
        ("application-participates-in-bounded-context", ref("application", application), ref("bounded-context", context)),
        ("application-supports-use-case", ref("application", application), ref("use-case", use_case)),
        ("bounded-context-defines-term", ref("bounded-context", context), ref("term", term_id)),
    ]
    return {
        "schemaVersion": 1, "openQuestions": [], "strategicCapabilities": [],
        "applications": [{"id": application, "name": "Loader", "purpose": "p", "assessment": assessment("loader-products-consumer")}],
        "deployables": [{"id": deployable, "name": "d", "kind": "worker", "assessment": assessment()}],
        "boundedContexts": [{"id": context, "name": "Catalog Ingestion", "description": "d", "assessment": assessment()}],
        "useCases": [{"id": use_case, "name": "Load products", "actor": "a", "outcome": "o", "assessment": assessment()}],
        "terms": [{"id": term_id, "name": term_name, "definition": "d", "assessment": assessment()}],
        "relationships": [{"id": relationship_prefix + str(index), "type": kind, "source": source, "target": target,
                           "statement": "s", "assessment": assessment()} for index, (kind, source, target) in enumerate(relationships)],
    }


class CatalogIdTests(unittest.TestCase):
    def test_slug_splits_camel_case_and_acronyms(self):
        self.assertEqual(slug("ProductPropalDTO"), "product-propal-dto")
        self.assertEqual(slug("baseDoble"), "base-doble")
        self.assertEqual(slug("Base Doble"), "base-doble")
        self.assertEqual(slug("Familia-a-seq"), "familia-a-seq")
        self.assertEqual(slug("UPA"), "upa")

    def test_two_runs_with_different_ids_converge(self):
        first, _ = normalize_ids(run("loader-app", "loader-runtime", "ctx-a", "uc-a", "ProductMessage", "pm", "x"))
        second, _ = normalize_ids(run("loader", "deployable-loader", "ctx-b", "uc-b", "productMessage", "message", "y"))
        self.assertEqual([i["id"] for i in first["applications"]], ["loader-products-consumer-app"])
        self.assertEqual(first["terms"][0]["id"], "product-message")
        self.assertEqual([i["id"] for i in first["deployables"]], ["loader-products-consumer-deployable"])
        self.assertEqual([r["id"] for r in first["relationships"]][0], "loader-products-consumer-app-comprises-loader-products-consumer-deployable")
        self.assertEqual(first["applications"], second["applications"])
        self.assertEqual(first["deployables"], second["deployables"])
        self.assertEqual([i["id"] for i in first["terms"]], [i["id"] for i in second["terms"]])
        self.assertEqual(convention_errors(first), [])

    def test_concepts_adopt_ids_from_the_current_catalog_by_name(self):
        current = run("loader-products-consumer-app", "loader-products-consumer-deployable", "catalog-ingestion-context",
                      "ingest-batch", "ProductMessage", "product-message", "r")
        normalized, renames = normalize_ids(run("a", "b", "ctx-b", "uc-b", "ProductMessage", "pm", "x"), copy.deepcopy(current))
        self.assertEqual(normalized["boundedContexts"][0]["id"], "catalog-ingestion-context")
        self.assertEqual(normalized["useCases"][0]["id"], "ingest-batch")
        # A relationship with the same type, source and target keeps the id the current catalog gave it.
        self.assertEqual({r["id"] for r in normalized["relationships"]}, {r["id"] for r in current["relationships"]})
        self.assertTrue(all(link["source"]["entityId"] in {"loader-products-consumer-app", "catalog-ingestion-context"}
                            for link in normalized["relationships"]))
        self.assertIn(("boundedContexts", "ctx-b", "catalog-ingestion-context"), renames)

    def test_ids_that_would_collide_are_refused(self):
        document = run("a", "b", "c", "u", "ProductMessage", "pm", "x")
        duplicate = copy.deepcopy(document["terms"][0])
        duplicate["id"] = "other"
        document["terms"].append(duplicate)
        with self.assertRaises(ValueError):
            normalize_ids(document)

    def test_convention_errors_name_the_departing_ids(self):
        errors = convention_errors(run("loader", "runtime", "c", "u", "ProductMessage", "pm", "x"))
        self.assertEqual(len(errors), 3)


if __name__ == "__main__":
    unittest.main()
