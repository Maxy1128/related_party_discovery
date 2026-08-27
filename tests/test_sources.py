from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from rpd.config import Settings
from rpd.db import connect, initialize
from rpd.http import Download
from rpd.models import OfficialSource
from rpd.repository import IdentityRepository
from rpd.sources.gleif import GleifClient
from rpd.sources.official import OfficialDocumentIngestor
from rpd.sources.wikidata import WikidataClient


class FakeJsonHttp:
    def __init__(self, responses):
        self.responses = responses

    def get_json(self, url, params=None):
        key = (url, tuple(sorted((params or {}).items())))
        return self.responses.get(key, self.responses.get(url))


class FakeDownloadHttp:
    def __init__(self, download):
        self.value = download

    def download(self, url, max_bytes=50 * 1024 * 1024):
        return self.value


class SourceAdapterTests(unittest.TestCase):
    def test_gleif_maps_identity_and_reporting_exceptions(self) -> None:
        settings = Settings.from_env({})
        lei = "213800YOEO5OQ72G2R82"
        record_url = f"{settings.gleif_api_base}/lei-records/{lei}"
        direct_url = "https://example.test/direct-exception"
        ultimate_url = "https://example.test/ultimate-exception"
        record = {
            "type": "lei-records",
            "id": lei,
            "attributes": {
                "lei": lei,
                "entity": {
                    "legalName": {"name": "RIO TINTO PLC"},
                    "otherNames": [{"name": "Rio Tinto plc"}],
                    "registeredAs": "00719885",
                    "registeredAt": {"id": "RA000585"},
                    "jurisdiction": "GB",
                    "legalAddress": {
                        "addressLines": ["6 St James's Square"],
                        "city": "London",
                        "postalCode": "SW1Y 4AD",
                        "country": "GB",
                    },
                },
            },
            "links": {"self": record_url},
            "relationships": {
                "direct-parent": {"links": {"reporting-exception": direct_url}},
                "ultimate-parent": {"links": {"reporting-exception": ultimate_url}},
            },
        }
        http = FakeJsonHttp(
            {
                record_url: {"data": record},
                direct_url: {"data": {"attributes": {"reason": "NATURAL_PERSONS"}}},
                ultimate_url: {"data": {"attributes": {"reason": "NATURAL_PERSONS"}}},
            }
        )
        profile = GleifClient(settings, http=http).get(lei)
        self.assertEqual(profile.lei, lei)
        self.assertEqual(profile.registration_number, "00719885")
        self.assertEqual(profile.country_code, "GB")
        self.assertEqual(len(profile.parents), 2)
        self.assertEqual(profile.parents[0].status, "REPORTING_EXCEPTION")

    def test_wikidata_only_maps_aliases_and_auxiliary_values(self) -> None:
        settings = Settings.from_env({})
        entity_id = "Q10291918"
        response = {
            "entities": {
                entity_id: {
                    "labels": {"en": {"value": "Rio Tinto plc"}},
                    "aliases": {"en": [{"value": "Rio Tinto PLC"}]},
                    "claims": {
                        "P1278": [
                            {
                                "mainsnak": {
                                    "snaktype": "value",
                                    "datavalue": {"value": "213800YOEO5OQ72G2R82"},
                                }
                            }
                        ],
                        "P856": [
                            {
                                "mainsnak": {
                                    "snaktype": "value",
                                    "datavalue": {"value": "https://www.riotinto.com/"},
                                }
                            }
                        ],
                        "P127": [
                            {
                                "mainsnak": {
                                    "snaktype": "value",
                                    "datavalue": {"value": {"id": "Q999"}},
                                }
                            }
                        ],
                    },
                }
            }
        }
        http = FakeJsonHttp({settings.wikidata_api_url: response})
        profile = WikidataClient(settings, http=http).get(entity_id)
        self.assertIsNone(profile.legal_name)
        self.assertEqual(profile.website, "https://www.riotinto.com/")
        self.assertIn(("WIKIDATA", entity_id), profile.identifiers)
        self.assertIn(("LEI", "213800YOEO5OQ72G2R82"), profile.identifiers)
        self.assertNotIn(("PARENT", "Q999"), profile.identifiers)

    def test_official_html_is_hashed_persisted_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env({"RPD_DATA_DIR": temp_dir})
            settings.paths.create()
            initialize(settings.paths.sqlite_path)
            download = Download(
                final_url="https://example.test/company",
                content=b"<html><script>ignore()</script><body><h1>Company</h1><p>Useful text.</p></body></html>",
                content_type="text/html",
                retrieved_at="2026-08-16T00:00:00+00:00",
                headers={},
            )
            changed_raw_same_text = Download(
                final_url="https://example.test/company",
                content=b"<html><script>changed()</script><body><h1>Company</h1><p>Useful text.</p></body></html>",
                content_type="text/html",
                retrieved_at="2026-08-16T00:01:00+00:00",
                headers={},
            )
            source = OfficialSource(
                key="company",
                title="Company disclosure",
                url="https://example.test/company",
                publisher="Example plc",
            )
            with connect(settings.paths.sqlite_path) as connection:
                ingestor = OfficialDocumentIngestor(
                    settings, connection, http=FakeDownloadHttp(download)
                )
                first = ingestor.ingest(source)
                second = OfficialDocumentIngestor(
                    settings, connection, http=FakeDownloadHttp(changed_raw_same_text)
                ).ingest(source)
                self.assertFalse(first.reused)
                self.assertTrue(second.reused)
                self.assertEqual(first.document_version_id, second.document_version_id)
                self.assertEqual(first.content_hash, second.content_hash)
                self.assertNotEqual(first.raw_content_hash, second.raw_content_hash)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0],
                    1,
                )
            normalized = settings.paths.root / first.normalized_path
            text = normalized.read_text(encoding="utf-8")
            self.assertIn("Useful text.", text)
            self.assertNotIn("ignore()", text)

    def test_identity_repository_reuses_lei(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings.from_env({"RPD_DATA_DIR": temp_dir})
            initialize(settings.paths.sqlite_path)
            gleif_http = FakeJsonHttp(
                {
                    f"{settings.gleif_api_base}/lei-records/LEI1": {
                        "data": {
                            "id": "LEI1",
                            "attributes": {
                                "lei": "LEI1",
                                "entity": {"legalName": {"name": "Example plc"}},
                            },
                        }
                    }
                }
            )
            profile = GleifClient(settings, http=gleif_http).get(
                "LEI1", include_parents=False
            )
            with connect(settings.paths.sqlite_path) as connection:
                repository = IdentityRepository(connection)
                first = repository.upsert(profile)
                second = repository.upsert(profile)
                self.assertEqual(first, second)
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0], 1
                )

    def test_pdf_parser_falls_back_when_pdftotext_fails(self) -> None:
        fake_page = MagicMock()
        fake_page.extract_text.return_value = "Fallback PDF text"
        fake_reader = MagicMock()
        fake_reader.pages = [fake_page]
        with patch("rpd.sources.official.shutil.which", return_value="pdftotext"), patch(
            "rpd.sources.official.subprocess.run",
            side_effect=__import__("subprocess").CalledProcessError(1, "pdftotext"),
        ), patch("rpd.sources.official.PdfReader", return_value=fake_reader):
            text = OfficialDocumentIngestor._pdf_text(Path("example.pdf"))
        self.assertEqual(text, "--- PAGE 1 ---\nFallback PDF text")


if __name__ == "__main__":
    unittest.main()
