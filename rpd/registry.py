"""Controlled relationship vocabulary with a governed open extension slot."""

from __future__ import annotations

import json
import sqlite3


RELATION_TYPES = (
    ("PARENT_OF", "Controls a subsidiary as its parent.", ["HAS_SUBSIDIARY"], "parent", "subsidiary", "DIRECTED", "CORPORATE_STRUCTURE"),
    ("SUBSIDIARY_OF", "Is controlled by a parent entity.", ["CONTROLLED_BY"], "subsidiary", "parent", "DIRECTED", "CORPORATE_STRUCTURE"),
    ("OWNS", "Holds a disclosed ownership interest in another entity.", ["SHAREHOLDER_OF", "EQUITY_OWNERSHIP"], "owner", "owned entity", "DIRECTED", "MANAGEMENT_OWNERSHIP"),
    ("JOINT_VENTURE_WITH", "Participates in a joint venture with another entity.", ["JV_WITH", "JOINT_VENTURE"], "joint venture partner", "joint venture partner", "UNDIRECTED", "CORPORATE_STRUCTURE"),
    ("ASSOCIATE_OF", "Is disclosed as an associate relationship.", [], "associate", "associate", "UNDIRECTED", "CORPORATE_STRUCTURE"),
    ("DIRECTOR_OF", "Serves as a director of an organization.", ["BOARD_MEMBER_OF"], "director", "organization", "DIRECTED", "MANAGEMENT_OWNERSHIP"),
    ("SUPPLIER_TO", "Supplies goods or services to an entity.", ["SUPPLIES"], "supplier", "customer", "DIRECTED", "COMMERCIAL"),
    ("CUSTOMER_OF", "Purchases goods or services from an entity.", ["OPERATIONAL_SUPPLY_CONNECTION"], "customer", "supplier", "DIRECTED", "COMMERCIAL"),
    ("CONTRACTOR_TO", "Acts as a contractor to an entity.", ["CONTRACTED_BY"], "contractor", "principal", "DIRECTED", "COMMERCIAL"),
    ("PARTNERED_WITH", "Has an explicit commercial partnership.", ["PARTNERS_WITH", "WORKS_WITH", "GOVERNMENT_PARTNERSHIP"], "partner", "partner", "UNDIRECTED", "COMMERCIAL"),
    ("POWER_PURCHASE_AGREEMENT_WITH", "Is party to a disclosed power purchase or offtake agreement.", ["POWER_OFFTAKE_AGREEMENT", "PPA_WITH"], "power purchaser", "power supplier", "DIRECTED", "COMMERCIAL"),
    ("MEMORANDUM_OF_UNDERSTANDING_WITH", "Is party to an explicit memorandum of understanding.", ["MEMORANDUM_OF_UNDERSTANDING", "MOU_WITH"], "MoU party", "MoU party", "UNDIRECTED", "COMMERCIAL"),
    ("OPERATES", "Operates a disclosed facility, project, or scheme.", [], "operator", "operated asset", "DIRECTED", "COMMERCIAL"),
    ("ACQUIRED", "Completed an acquisition of another entity.", [], "acquirer", "target", "DIRECTED", "COMMERCIAL"),
    ("INVESTED_IN", "Made a disclosed investment in another entity.", [], "investor", "investee", "DIRECTED", "COMMERCIAL"),
    ("SUBJECT_TO_INVESTIGATION", "Is explicitly subject to an investigation.", ["INVESTIGATED_BY"], "subject", "authority", "DIRECTED", "REGULATORY_RISK"),
    ("SUED_BY", "Is the defendant or respondent in legal proceedings.", ["LITIGATION_WITH"], "defendant", "claimant", "DIRECTED", "REGULATORY_RISK"),
    ("SANCTIONED_BY", "Has a sanctions-list designation by an authority.", [], "designated entity", "authority", "DIRECTED", "REGULATORY_RISK"),
    ("DEBARRED_BY", "Has a debarment-list record by an authority.", [], "debarred entity", "authority", "DIRECTED", "REGULATORY_RISK"),
    ("CO_MENTION", "Entities only occur in the same document.", [], "mentioned entity", "mentioned entity", "UNDIRECTED", "OTHER"),
    ("OTHER_MATERIAL_RELATION", "Material explicit relation pending vocabulary governance.", [], "subject", "object", "DIRECTED", "OTHER"),
)


def seed_relation_registry(connection: sqlite3.Connection) -> None:
    for name, definition, aliases, subject, obj, direction, family in RELATION_TYPES:
        connection.execute(
            """INSERT INTO relation_type_registry(
               canonical_name,definition,aliases_json,subject_role,object_role,
               direction,relation_family,registry_status)
               VALUES (?,?,?,?,?,?,?,'ACTIVE')
               ON CONFLICT(canonical_name) DO UPDATE SET
                 definition=excluded.definition,aliases_json=excluded.aliases_json,
                 subject_role=excluded.subject_role,object_role=excluded.object_role,
                 direction=excluded.direction,relation_family=excluded.relation_family""",
            (name, definition, json.dumps(aliases), subject, obj, direction, family),
        )


def resolve_relation_type(connection: sqlite3.Connection, proposed: str) -> tuple[int, str]:
    key = proposed.strip().upper().replace(" ", "_")
    rows = connection.execute(
        "SELECT id,canonical_name,aliases_json FROM relation_type_registry WHERE registry_status='ACTIVE'"
    ).fetchall()
    for row in rows:
        aliases = {str(value).upper() for value in json.loads(row["aliases_json"])}
        if key == row["canonical_name"] or key in aliases:
            return int(row["id"]), row["canonical_name"]
    other = next(row for row in rows if row["canonical_name"] == "OTHER_MATERIAL_RELATION")
    return int(other["id"]), other["canonical_name"]
