from __future__ import annotations

import json
import tempfile
import unittest

from rpd.config import Settings
from rpd.db import connect, initialize
from rpd.description_schema import (
    EntityDescriptionItem,
    EntityDescriptionPayload,
    RelationshipDescriptionItem,
    RelationshipDescriptionPayload,
)
from rpd.descriptions import DescriptionService


class FakeDescriptionLlm:
    def __init__(self):
        self.calls = 0

    def parse(self, messages, output_type):
        self.calls += 1
        contexts = json.loads(messages[-1]["content"].split("JSON:\n", 1)[1])
        if output_type is EntityDescriptionPayload:
            return EntityDescriptionPayload(entities=[
                EntityDescriptionItem(
                    entity_id=item["entity_id"],
                    description=f"Entity {item['entity_id']} has a documented role in this investigation.",
                ) for item in contexts
            ])
        return RelationshipDescriptionPayload(relationships=[
            RelationshipDescriptionItem(
                assertion_id=item["assertion_id"],
                description=(
                    f"{item['subject_name']} has a documented {item['normalized_relation_type']} "
                    f"relationship with {item['object_name']}."
                ),
            ) for item in contexts
        ])


class DescriptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings.from_env({"RPD_DATA_DIR": self.temp.name, "GRAPHRAG_LLM_MODEL": "test-model"})
        self.settings.paths.create()
        initialize(self.settings.paths.sqlite_path)

    def tearDown(self):
        self.temp.cleanup()

    def test_existing_entities_and_relationships_are_versioned_and_cached(self):
        with connect(self.settings.paths.sqlite_path) as connection:
            target = connection.execute(
                "INSERT INTO entities(canonical_name,normalized_name,lei) VALUES ('Target plc','target plc','LEI-TARGET')"
            ).lastrowid
            partner = connection.execute(
                "INSERT INTO entities(canonical_name,normalized_name) VALUES ('Partner Ltd','partner ltd')"
            ).lastrowid
            investigation_id = connection.execute(
                "INSERT INTO investigations(target_entity_id,title,status,parameters_json) VALUES (?,'Test','COMPLETED','{}')",
                (target,),
            ).lastrowid
            assertion_id = connection.execute(
                """INSERT INTO assertions(subject_entity_id,object_entity_id,
                   normalized_relation_type,classification,assertion_text,
                   explicit_or_inferred,validation_status,relationship_confidence)
                   VALUES (?,?,'PARTNERED_WITH','COUNTERPARTY',
                   'Target plc partnered with Partner Ltd.','EXPLICIT','VALIDATED','HIGH')""",
                (target, partner),
            ).lastrowid
            connection.execute(
                "INSERT INTO investigation_assertions(investigation_id,assertion_id) VALUES (?,?)",
                (investigation_id, assertion_id),
            )
            llm = FakeDescriptionLlm()
            service = DescriptionService(self.settings, connection, llm=llm)
            first = service.generate(investigation_id)
            self.assertEqual((first.entity_descriptions, first.relationship_descriptions), (2, 1))
            self.assertEqual(llm.calls, 2)
            second = service.generate(investigation_id)
            self.assertEqual(second.cached, 3)
            self.assertEqual(llm.calls, 2)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM entity_descriptions WHERE is_current=1").fetchone()[0], 2
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM relationship_descriptions WHERE is_current=1").fetchone()[0], 1
            )

            connection.execute(
                "UPDATE assertions SET assertion_text='Updated supported relationship context.' WHERE id=?",
                (assertion_id,),
            )
            third = service.generate(investigation_id)
            self.assertEqual(third.relationship_descriptions, 1)
            self.assertEqual(third.entity_descriptions, 2)
            self.assertEqual(third.cached, 0)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM entity_descriptions").fetchone()[0], 4
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM relationship_descriptions").fetchone()[0], 2
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM relationship_descriptions WHERE is_current=1").fetchone()[0], 1
            )

    def test_invalid_output_rolls_back_all_description_changes(self):
        class InvalidLlm(FakeDescriptionLlm):
            def parse(self, messages, output_type):
                if output_type is EntityDescriptionPayload:
                    return EntityDescriptionPayload(entities=[])
                return super().parse(messages, output_type)

        with connect(self.settings.paths.sqlite_path) as connection:
            entity_id = connection.execute(
                "INSERT INTO entities(canonical_name,normalized_name) VALUES ('Target','target')"
            ).lastrowid
            investigation_id = connection.execute(
                "INSERT INTO investigations(target_entity_id,title,status,parameters_json) VALUES (?,'Test','COMPLETED','{}')",
                (entity_id,),
            ).lastrowid
            with self.assertRaises(ValueError):
                DescriptionService(self.settings, connection, llm=InvalidLlm()).generate(investigation_id)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM entity_descriptions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
