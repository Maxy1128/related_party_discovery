from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rpd.config import Settings
from rpd.db import connect, initialize
from rpd.watchlists import (
    WatchlistMatcher, WatchlistRecord, WatchlistSnapshot,
    parse_ofac_xml, parse_uk_csv, parse_world_bank_html,
)


class WatchlistTests(unittest.TestCase):
    def test_official_format_parsers(self):
        ofac = b"""<sdnList><sdnEntry><uid>10</uid><firstName>Example</firstName><lastName>Industries</lastName><sdnType>Entity</sdnType><akaList><aka><firstName>Example</firstName><lastName>Group</lastName></aka></akaList><addressList><address><country>Freedonia</country><city>Capital</city></address></addressList><idList><id><idType>Registration ID</idType><idNumber>REG-10</idNumber></id></idList></sdnEntry></sdnList>"""
        ofac_record = parse_ofac_xml(ofac, "https://ofac.test/list")[0]
        self.assertEqual((ofac_record.record_id, ofac_record.primary_name), ("10", "Example Industries"))
        self.assertIn("Example Group", ofac_record.aliases)
        self.assertIn(("Registration ID", "REG-10"), ofac_record.identifiers)

        uk = "Unique ID,Name 1,Name 2,Entity Type,Country,Regime Name\nUK001,Example,Trading Ltd,Entity,Freedonia,Test Regime\n".encode()
        uk_record = parse_uk_csv(uk, "https://uk.test/list")[0]
        self.assertEqual(uk_record.primary_name, "Example Trading Ltd")
        self.assertEqual(uk_record.countries, ("Freedonia",))

        world_bank = b"""<table><tr><th>Name of Firm/Individual</th><th>Address</th><th>Country</th><th>Grounds</th></tr><tr><td>Example Builder</td><td>Main Street</td><td>Freedonia</td><td>Fraud</td></tr></table>"""
        wb_record = parse_world_bank_html(world_bank, "https://worldbank.test/list")[0]
        self.assertEqual((wb_record.primary_name, wb_record.details), ("Example Builder", "Fraud"))

    def test_matching_never_creates_a_company_relationship(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.from_env({"RPD_DATA_DIR": directory})
            settings.paths.create()
            initialize(settings.paths.sqlite_path)
            with connect(settings.paths.sqlite_path) as connection:
                entity_id = connection.execute(
                    "INSERT INTO entities(canonical_name,normalized_name,country_code) VALUES ('Example Industries','example industries','Freedonia')"
                ).lastrowid
                records = (
                    WatchlistRecord("OFAC", "exact-country", "Example Industries", countries=("Freedonia",), source_url="https://example.test"),
                    WatchlistRecord("OFAC", "exact-only", "Example Industries", source_url="https://example.test"),
                    WatchlistRecord("OFAC", "fuzzy", "Example Industrie", source_url="https://example.test"),
                    WatchlistRecord("OFAC", "other", "Unrelated Person", source_url="https://example.test"),
                )
                snapshot = WatchlistSnapshot("OFAC", records, "2026-08-16T00:00:00Z", Path(directory) / "list.xml", "https://example.test")
                match_ids = WatchlistMatcher(connection, 0.88).match(entity_id, snapshot)
                self.assertEqual(len(match_ids), 3)
                statuses = {row["list_record_id"]: row["match_status"] for row in connection.execute("SELECT * FROM watchlist_matches")}
                self.assertEqual(statuses["exact-country"], "CONFIRMED")
                self.assertEqual(statuses["exact-only"], "POTENTIAL")
                self.assertEqual(statuses["fuzzy"], "POTENTIAL")
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM assertions").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
