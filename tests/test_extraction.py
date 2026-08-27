from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import requests

from rpd.config import Settings
from rpd.db import connect, initialize
from rpd.extraction import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    ExtractionService,
    TextChunk,
    chunk_text,
)
from rpd.extraction_schema import ExtractionPayload
from rpd.llm import OpenAICompatibleClient, StructuredOutputError


EMPTY_PAYLOAD = {
    "entities": [],
    "mentions": [],
    "relationships": [],
    "risk_events": [],
    "document_date": None,
    "ambiguity_flags": [],
}


class FakePostHttp:
    def __init__(self, content):
        self.content = content
        self.calls = []

    def post_json(self, url, payload, headers=None, timeout=None):
        self.calls.append((url, payload, headers, timeout))
        return {"choices": [{"message": {"content": json.dumps(self.content)}}]}


class FakeLlm:
    def __init__(self):
        self.calls = 0

    def parse(self, messages, output_type):
        self.calls += 1
        return output_type.model_validate(EMPTY_PAYLOAD)


class FailingLlm:
    def parse(self, messages, output_type):
        raise RuntimeError("provider unavailable")


class SizeLimitedLlm:
    def __init__(self, limit=10_000):
        self.limit = limit
        self.source_sizes = []

    def parse(self, messages, output_type):
        content = messages[-1]["content"]
        source = content.split("<SOURCE_DOCUMENT>\n", 1)[1].rsplit(
            "\n</SOURCE_DOCUMENT>", 1
        )[0]
        self.source_sizes.append(len(source))
        if len(source) > self.limit:
            raise requests.exceptions.SSLError("gateway closed large request")
        evidence = source[: min(5, len(source))]
        return output_type.model_validate({
            "entities": [{
                "local_id": "e1", "name": "Example", "entity_type": "ORGANIZATION",
                "entity_scope": "GROUP", "aliases": [], "identifiers": [],
                "country_code": None, "ambiguous": True, "ambiguity_flags": [],
            }],
            "mentions": [{
                "entity_local_id": "e1", "mention_text": evidence,
                "context_text": evidence, "evidence_text": evidence,
            }],
            "relationships": [], "risk_events": [],
            "document_date": None, "ambiguity_flags": [],
        })


def insert_document(connection, settings, url, text):
    digest = hashlib.sha256(text.encode()).hexdigest()
    path = settings.paths.normalized / "tests" / f"{digest}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    document_id = connection.execute(
        """INSERT INTO documents(source_type,title,original_url,normalized_url,first_retrieved_at)
           VALUES ('TEST','Test document',?,?,?)""",
        (url, url, "2026-08-16T00:00:00Z"),
    ).lastrowid
    return connection.execute(
        """INSERT INTO document_versions(
           document_id,content_hash,raw_content_hash,normalized_path,retrieval_status,retrieved_at)
           VALUES (?,?,?,?,?,?)""",
        (document_id, digest, digest, path.relative_to(settings.paths.root).as_posix(),
         "FULL_TEXT", "2026-08-16T00:00:00Z"),
    ).lastrowid


class ExtractionTests(unittest.TestCase):
    def test_chunking_is_stable_and_overlapping(self) -> None:
        text = "A" * 75
        chunks = chunk_text(text, max_chars=30, overlap_chars=5)
        self.assertEqual([(c.start_offset, c.end_offset) for c in chunks], [(0, 30), (25, 55), (50, 75)])
        self.assertEqual(chunks[0].content_hash, hashlib.sha256(("A" * 30).encode()).hexdigest())

    def test_large_provider_failure_splits_chunk_and_rebases_local_ids(self) -> None:
        settings = Settings.from_env({})
        llm = SizeLimitedLlm(limit=10_000)
        service = ExtractionService(settings, sqlite3.connect(":memory:"), llm=llm)
        text = "Alpha " * 3_334
        chunk = TextChunk(
            index=0, start_offset=0, end_offset=len(text), text=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
        )
        payload = service._parse_chunk_adaptive(
            chunk,
            {"title": "Annual Report", "published_at": None, "original_url": None},
        )
        self.assertGreater(len(payload.entities), 1)
        self.assertEqual(
            len({entity.local_id for entity in payload.entities}),
            len(payload.entities),
        )
        self.assertIn("ADAPTIVE_SUBCHUNK_EXTRACTION", payload.ambiguity_flags)
        self.assertGreater(llm.source_sizes[0], 10_000)
        self.assertTrue(all(size <= 10_000 for size in llm.source_sizes[1:]))

    def test_pdf_resume_preserves_new_and_legacy_chunk_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = replace(
                Settings.from_env({"RPD_DATA_DIR": temp_dir}),
                extraction_chunk_chars=20_000,
                extraction_chunk_overlap_chars=500,
            )
            settings.paths.create()
            initialize(settings.paths.sqlite_path)
            with connect(settings.paths.sqlite_path) as connection:
                version_id = insert_document(
                    connection, settings, "https://test/annual", "annual report text"
                )
                run_id = connection.execute(
                    """INSERT INTO extraction_runs(
                       document_version_id,model,prompt_version,schema_version,status)
                       VALUES (?,?,?,?,'FAILED')""",
                    (version_id, settings.llm_model, PROMPT_VERSION, SCHEMA_VERSION),
                ).lastrowid
                service = ExtractionService(settings, connection, llm=FakeLlm())
                document = {"media_type": "application/pdf", "title": "Annual Report"}
                self.assertEqual(service._initial_chunk_chars(document, run_id), 10_000)
                connection.execute(
                    """INSERT INTO extraction_chunks(
                       extraction_run_id,chunk_index,start_offset,end_offset,content_hash,
                       status,response_json)
                       VALUES (?,0,0,10000,'hash','SUCCEEDED','{}')""",
                    (run_id,),
                )
                self.assertEqual(service._initial_chunk_chars(document, run_id), 10_000)
                connection.execute(
                    "UPDATE extraction_chunks SET end_offset=15000 WHERE extraction_run_id=?",
                    (run_id,),
                )
                self.assertEqual(service._initial_chunk_chars(document, run_id), 20_000)

    def test_chat_request_uses_strict_json_schema_without_key_in_body(self) -> None:
        settings = Settings.from_env({"GRAPHRAG_API_KEY": "secret-value"})
        http = FakePostHttp(EMPTY_PAYLOAD)
        result = OpenAICompatibleClient(settings, http=http).parse(
            [{"role": "user", "content": "test"}], ExtractionPayload
        )
        self.assertEqual(result.entities, [])
        body = http.calls[0][1]
        self.assertEqual(body["model"], "gpt-5.4")
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        schema = body["response_format"]["json_schema"]["schema"]
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertNotIn("secret-value", json.dumps(body))
        self.assertEqual(http.calls[0][3], (10, 180))

    def test_invalid_structured_output_is_rejected(self) -> None:
        settings = Settings.from_env({"GRAPHRAG_API_KEY": "secret-value"})
        invalid = dict(EMPTY_PAYLOAD, unexpected=True)
        with self.assertRaises(StructuredOutputError):
            OpenAICompatibleClient(settings, http=FakePostHttp(invalid)).parse(
                [{"role": "user", "content": "test"}], ExtractionPayload
            )

    def test_document_and_content_cache_avoid_repeat_llm_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = replace(
                Settings.from_env({"RPD_DATA_DIR": temp_dir}),
                extraction_chunk_chars=30,
                extraction_chunk_overlap_chars=5,
            )
            settings.paths.create()
            initialize(settings.paths.sqlite_path)
            llm = FakeLlm()
            text = "A" * 75
            with connect(settings.paths.sqlite_path) as connection:
                first_id = insert_document(connection, settings, "https://test/one", text)
                second_id = insert_document(connection, settings, "https://test/two", text)
                service = ExtractionService(settings, connection, llm=llm)
                first = service.extract_document(first_id)
                calls_after_first = llm.calls
                self.assertEqual(calls_after_first, 2)
                self.assertEqual(service.extract_document(first_id), first)
                self.assertEqual(llm.calls, calls_after_first)
                second = service.extract_document(second_id)
                self.assertEqual(second, first)
                self.assertEqual(llm.calls, calls_after_first)
                copied = connection.execute(
                    "SELECT cache_source_run_id FROM extraction_runs WHERE document_version_id=?",
                    (second_id,),
                ).fetchone()[0]
                self.assertIsNotNone(copied)

    def test_evidence_must_be_verbatim_and_entity_references_must_exist(self) -> None:
        payload = ExtractionPayload.model_validate(
            {
                "entities": [],
                "mentions": [{
                    "entity_local_id": "missing", "mention_text": "Example",
                    "context_text": "context", "evidence_text": "invented evidence"
                }],
                "relationships": [], "risk_events": [],
                "document_date": None, "ambiguity_flags": [],
            }
        )
        with self.assertRaises(ValueError):
            ExtractionService._validate_payload(payload, "actual source")

    def test_whitespace_only_evidence_difference_is_replaced_by_exact_source_span(self) -> None:
        source = "Rio Tinto entered into a 50:50\n\tjoint venture with the Government."
        candidate = "Rio Tinto entered into a 50:50 joint venture with the Government."
        self.assertEqual(
            ExtractionService._exact_evidence_span(candidate, source), source
        )

    def test_ambiguous_whitespace_match_is_rejected(self) -> None:
        source = "Same\ntext and Same\ttext"
        self.assertIsNone(
            ExtractionService._exact_evidence_span("Same text", source)
        )

    def test_unique_token_alignment_returns_real_source_punctuation(self) -> None:
        source = (
            "Rio Tinto’s Canadian subsidiary entered a long-term, 30-year "
            "power-purchase agreement with Example Energy."
        )
        candidate = (
            "Rio Tinto's Canadian subsidiary entered a long term 30 year "
            "power purchase agreement with Example Energy."
        )
        self.assertEqual(
            ExtractionService._exact_evidence_span(candidate, source),
            source[:-1],
        )

    def test_repeated_token_aligned_passage_is_rejected(self) -> None:
        passage = "Alpha entered a long term power purchase agreement with Beta"
        source = f"{passage}. Later, {passage}."
        candidate = "Alpha entered a long-term power-purchase agreement with Beta"
        self.assertIsNone(
            ExtractionService._exact_evidence_span(candidate, source)
        )

    def test_provider_failure_is_recorded_without_partial_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env({"RPD_DATA_DIR": temp_dir})
            settings.paths.create()
            initialize(settings.paths.sqlite_path)
            with connect(settings.paths.sqlite_path) as connection:
                version_id = insert_document(
                    connection, settings, "https://test/failure", "document text"
                )
                with self.assertRaises(RuntimeError):
                    ExtractionService(
                        settings, connection, llm=FailingLlm()
                    ).extract_document(version_id)
                run = connection.execute(
                    "SELECT status,response_json,error_message FROM extraction_runs"
                ).fetchone()
                self.assertEqual(run["status"], "FAILED")
                self.assertIsNone(run["response_json"])
                self.assertIn("provider unavailable", run["error_message"])


if __name__ == "__main__":
    unittest.main()
