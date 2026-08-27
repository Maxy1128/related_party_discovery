"""Read-only investigation views, reports, timelines, and bounded graphs."""

from __future__ import annotations

import html
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

import networkx as nx


BOUNDARY_NOTICE = (
    "This system discovers publicly disclosed relationships and risk associations. "
    "It does not claim complete coverage of undisclosed customers, suppliers, "
    "counterparties, or related parties."
)


@dataclass(frozen=True)
class InvestigationView:
    investigation: dict
    profile: dict
    relationships: tuple[dict, ...]
    risk_events: tuple[dict, ...]
    watchlist_matches: tuple[dict, ...]
    documents: tuple[dict, ...]
    steps: tuple[dict, ...]

    @property
    def timeline(self) -> tuple[dict, ...]:
        items = []
        for relation in self.relationships:
            item = dict(relation)
            item["kind"] = "Relationship"
            item["timeline_date"] = relation.get("event_date") or relation.get("published_at") or relation.get("retrieved_at")
            item["date_inferred"] = not bool(relation.get("event_date"))
            items.append(item)
        for event in self.risk_events:
            item = dict(event)
            item["kind"] = "Risk event"
            item["timeline_date"] = event.get("event_date") or event.get("published_at") or event.get("retrieved_at")
            item["date_inferred"] = not bool(event.get("event_date"))
            items.append(item)
        return tuple(sorted(items, key=lambda item: item.get("timeline_date") or "9999"))


class ReportBuilder:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def load(self, investigation_id: int) -> InvestigationView:
        investigation = self.connection.execute(
            "SELECT * FROM investigations WHERE id=?", (investigation_id,)
        ).fetchone()
        if not investigation:
            raise LookupError(f"Investigation not found: {investigation_id}")
        profile = self.connection.execute(
            """SELECT e.*,ed.description entity_description,
                      ed.generation_method entity_description_method
               FROM entities e LEFT JOIN entity_descriptions ed
                 ON ed.investigation_id=? AND ed.entity_id=e.id AND ed.is_current=1
               WHERE e.id=?""",
            (investigation_id, investigation["target_entity_id"]),
        ).fetchone()
        relationships = self.connection.execute(
            """SELECT a.*,s.canonical_name subject_name,o.canonical_name object_name,
                      s.entity_scope subject_scope,s.entity_type subject_type,
                      s.lei subject_lei,s.country_code subject_country_code,
                      s.registered_address subject_registered_address,
                      s.ambiguous subject_ambiguous,
                      o.entity_scope object_scope,o.entity_type object_type,
                      o.lei object_lei,o.country_code object_country_code,
                      o.registered_address object_registered_address,
                      o.ambiguous object_ambiguous,
                      e.evidence_text,e.evidence_quality,d.original_url,d.title document_title,
                      rt.definition relation_definition,
                      rd.description relationship_description,
                      rd.generation_method relationship_description_method,
                      sd.description subject_description,od.description object_description
               FROM investigation_assertions ia JOIN assertions a ON a.id=ia.assertion_id
               JOIN entities s ON s.id=a.subject_entity_id
               LEFT JOIN entities o ON o.id=a.object_entity_id
               LEFT JOIN relation_type_registry rt ON rt.id=a.relation_type_id
               LEFT JOIN evidence e ON e.assertion_id=a.id AND e.supports_assertion=1
               LEFT JOIN document_versions v ON v.id=e.document_version_id
               LEFT JOIN documents d ON d.id=v.document_id
               LEFT JOIN relationship_descriptions rd
                 ON rd.investigation_id=ia.investigation_id
                 AND rd.assertion_id=a.id AND rd.is_current=1
               LEFT JOIN entity_descriptions sd
                 ON sd.investigation_id=ia.investigation_id
                 AND sd.entity_id=a.subject_entity_id AND sd.is_current=1
               LEFT JOIN entity_descriptions od
                 ON od.investigation_id=ia.investigation_id
                 AND od.entity_id=a.object_entity_id AND od.is_current=1
               WHERE ia.investigation_id=? ORDER BY a.id""", (investigation_id,)
        ).fetchall()
        risk_events = self.connection.execute(
            """SELECT r.*,e.canonical_name entity_name,a.relationship_confidence,
                      ev.evidence_text,d.original_url
               FROM risk_events r JOIN entities e ON e.id=r.entity_id
               LEFT JOIN assertions a ON a.id=r.assertion_id
               LEFT JOIN evidence ev ON ev.assertion_id=a.id
               LEFT JOIN document_versions v ON v.id=ev.document_version_id
               LEFT JOIN documents d ON d.id=v.document_id
               WHERE a.id IN (SELECT assertion_id FROM investigation_assertions WHERE investigation_id=?)
               ORDER BY r.id""", (investigation_id,)
        ).fetchall()
        entity_ids = {investigation["target_entity_id"]}
        for row in relationships:
            entity_ids.add(row["subject_entity_id"])
            if row["object_entity_id"] is not None:
                entity_ids.add(row["object_entity_id"])
        placeholders = ",".join("?" for _ in entity_ids)
        watchlist_matches = self.connection.execute(
            f"""SELECT w.*,e.canonical_name entity_name FROM watchlist_matches w
                 JOIN entities e ON e.id=w.entity_id WHERE w.entity_id IN ({placeholders})
                 ORDER BY CASE w.match_status WHEN 'CONFIRMED' THEN 0 ELSE 1 END,w.id""",
            tuple(sorted(entity_ids)),
        ).fetchall()
        documents = self.connection.execute(
            """SELECT d.*,v.id document_version_id,v.retrieval_status,v.retrieved_at
               FROM investigation_documents i JOIN documents d ON d.id=i.document_id
               LEFT JOIN document_versions v ON v.document_id=d.id AND v.is_current=1
               WHERE i.investigation_id=? ORDER BY COALESCE(d.published_at,v.retrieved_at) DESC""",
            (investigation_id,),
        ).fetchall()
        steps = self.connection.execute(
            "SELECT * FROM investigation_steps WHERE investigation_id=? ORDER BY id", (investigation_id,)
        ).fetchall()
        return InvestigationView(
            dict(investigation), dict(profile) if profile else {}, tuple(map(dict, relationships)),
            tuple(map(dict, risk_events)), tuple(map(dict, watchlist_matches)),
            tuple(map(dict, documents)), tuple(map(dict, steps)),
        )

    def graph(self, view: InvestigationView) -> nx.MultiDiGraph:
        graph = nx.MultiDiGraph()
        target_id = view.profile.get("id")
        if target_id is not None:
            graph.add_node(
                target_id, label=view.profile.get("canonical_name", "Target"),
                description=view.profile.get("entity_description"), target=True, risk=False,
                scope=view.profile.get("entity_scope"), type=view.profile.get("entity_type"),
                lei=view.profile.get("lei"), country=view.profile.get("country_code"),
                address=view.profile.get("registered_address"),
                ambiguous=bool(view.profile.get("ambiguous")),
            )
        risk_ids = {item["entity_id"] for item in view.risk_events}
        risk_ids.update(item["entity_id"] for item in view.watchlist_matches)
        first_hop: set[int] = set()
        for relation in view.relationships:
            subject, obj = relation["subject_entity_id"], relation["object_entity_id"]
            if obj is None:
                continue
            if target_id in (subject, obj):
                first_hop.update((subject, obj))
                self._add_relation(graph, relation, risk_ids)
        # Limited two-hop experiment: include only a second-hop node with a recorded risk signal.
        for relation in view.relationships:
            subject, obj = relation["subject_entity_id"], relation["object_entity_id"]
            if obj is None or target_id in (subject, obj):
                continue
            if (subject in first_hop and obj in risk_ids) or (obj in first_hop and subject in risk_ids):
                self._add_relation(graph, relation, risk_ids)
        return graph

    @staticmethod
    def _add_relation(graph: nx.MultiDiGraph, relation: dict, risk_ids: set[int]) -> None:
        for key in ("subject", "object"):
            entity_id = relation[f"{key}_entity_id"]
            prior = graph.nodes[entity_id] if entity_id in graph else {}
            graph.add_node(
                entity_id, label=relation[f"{key}_name"],
                description=relation.get(f"{key}_description"),
                target=prior.get("target", False), risk=entity_id in risk_ids,
                scope=relation.get(f"{key}_scope"), type=relation.get(f"{key}_type"),
                lei=relation.get(f"{key}_lei"),
                country=relation.get(f"{key}_country_code"),
                address=relation.get(f"{key}_registered_address"),
                ambiguous=bool(relation.get(f"{key}_ambiguous")),
            )
        graph.add_edge(
            relation["subject_entity_id"], relation["object_entity_id"],
            label=relation["normalized_relation_type"], confidence=relation["relationship_confidence"],
            proposed_relation_type=relation.get("proposed_relation_type"),
            assertion_text=relation.get("assertion_text"),
            classification=relation["classification"],
            description=relation.get("relationship_description"),
            assertion_id=relation.get("id"),
            definition=relation.get("relation_definition"),
            validation_status=relation.get("validation_status"),
            explicit_or_inferred=relation.get("explicit_or_inferred"),
            event_date=relation.get("event_date"), valid_from=relation.get("valid_from"),
            valid_to=relation.get("valid_to"), evidence=relation.get("evidence_text"),
            evidence_quality=relation.get("evidence_quality"),
            source_url=relation.get("original_url"),
            source_title=relation.get("document_title"),
        )

    def markdown(self, view: InvestigationView) -> str:
        name = view.profile.get("canonical_name") or view.investigation["title"]
        lines = [f"# Public-Source Relationship Report: {name}", "", f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}", "",
                 "> " + BOUNDARY_NOTICE, "", "## Company Profile", "",
                 f"- Legal name: {view.profile.get('legal_name') or name}",
                 f"- LEI: {view.profile.get('lei') or 'Not available'}",
                 f"- Country: {view.profile.get('country_code') or 'Not available'}",
                 f"- Registered address: {view.profile.get('registered_address') or 'Not available'}",
                 f"- Investigation status: {view.investigation.get('status') or 'Unknown'}", ""]
        if view.profile.get("entity_description"):
            lines.extend([
                f"- AI-generated summary: {view.profile['entity_description']}", "",
            ])
        sections = (
            ("Validated Related Parties", [r for r in view.relationships if r["classification"] == "RELATED_PARTY" and r["validation_status"] == "VALIDATED"]),
            ("Commercial and Counterparty Relationships", [r for r in view.relationships if r["classification"] == "COUNTERPARTY"]),
            ("Unverified Leads", [r for r in view.relationships if r["relationship_confidence"] in ("LOW", "UNVERIFIED")]),
        )
        for title, rows in sections:
            lines.extend([f"## {title}", ""])
            if not rows:
                lines.extend(["No records available.", ""])
            for row in rows:
                lines.extend([
                    f"### {row['subject_name']} — {row['normalized_relation_type']} — {row.get('object_name') or 'Not specified'}",
                    "", f"- Confidence: {row['relationship_confidence']}",
                    f"- AI-generated summary: {row.get('relationship_description') or 'Not available'}",
                    f"- Event date: {row.get('event_date') or 'Not stated'}",
                    f"- Validity: {row.get('valid_from') or 'Not stated'} to {row.get('valid_to') or 'open/unknown'}",
                    f"- Evidence ({row.get('evidence_quality') or 'EXACT'}): {self._excerpt(row.get('evidence_text'))}",
                    f"- Source: {self._source_link(row.get('original_url'))}", "",
                ])
        lines.extend(["## Risk Associations and Alerts", ""])
        if not view.risk_events and not view.watchlist_matches:
            lines.extend(["No records available.", ""])
        for event in view.risk_events:
            lines.extend([f"- **{event['risk_severity']}** — {event['entity_name']}: {event['description']} ({event.get('event_date') or event.get('published_at') or 'date unavailable'})"])
        for match in view.watchlist_matches:
            lines.extend([f"- **{match['match_status']}** — {match['entity_name']} / {match['list_name']}: {match['rationale']} [Source]({match['source_url']})"])
        lines.extend(["", "## Chronological Timeline", ""])
        for item in view.timeline:
            description = item.get("assertion_text") or item.get("description")
            suffix = " (publication/retrieval date; inferred)" if item["date_inferred"] else ""
            lines.append(f"- {item.get('timeline_date') or 'Date unavailable'}{suffix}: {description}")
        lines.extend(["", "## Data Limitations", "", BOUNDARY_NOTICE, "",
                      "Metadata-only documents, titles, summaries, and co-mentions do not independently support validated conclusions.",
                      "AI-generated descriptions are presentation summaries only; they do not replace evidence or affect confidence."])
        pipeline_notes = [
            step for step in view.steps
            if step.get("status") != "COMPLETED"
            or re.search(r"\b[1-9]\d*\s+failed\b", step.get("message") or "", re.IGNORECASE)
        ]
        for step in pipeline_notes:
            lines.append(
                f"- Pipeline note — {step['step_name']} ({step['status']}): "
                f"{step.get('message') or 'No details available.'}"
            )
        return "\n".join(lines)

    def html(self, view: InvestigationView) -> str:
        markdown_text = self.markdown(view)
        blocks = []
        in_list = False
        for raw_line in markdown_text.splitlines():
            line = self._inline_html(raw_line)
            if line.startswith("# "):
                if in_list: blocks.append("</ul>"); in_list = False
                blocks.append(f"<h1>{line[2:]}</h1>")
            elif line.startswith("## "):
                if in_list: blocks.append("</ul>"); in_list = False
                blocks.append(f"<h2>{line[3:]}</h2>")
            elif line.startswith("### "):
                if in_list: blocks.append("</ul>"); in_list = False
                blocks.append(f"<h3>{line[4:]}</h3>")
            elif line.startswith("- "):
                if not in_list: blocks.append("<ul>"); in_list = True
                blocks.append(f"<li>{line[2:]}</li>")
            elif line.startswith("&gt; "):
                if in_list: blocks.append("</ul>"); in_list = False
                blocks.append(f"<aside>{line[5:]}</aside>")
            elif line:
                if in_list: blocks.append("</ul>"); in_list = False
                blocks.append(f"<p>{line}</p>")
        if in_list:
            blocks.append("</ul>")
        body = "\n".join(blocks)
        return f"""<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Relationship Report</title><style>body{{font:16px/1.55 Arial,sans-serif;max-width:1080px;margin:40px auto;padding:0 24px;color:#18212b}}h1,h2{{color:#123b5d}}h2{{border-bottom:1px solid #ccd7e0;padding-bottom:6px}}aside{{background:#fff4d6;border-left:5px solid #e5a100;padding:14px}}li{{margin:7px 0}}@media print{{body{{margin:0}}}}</style></head><body>{body}</body></html>"""

    @staticmethod
    def _excerpt(value: str | None, limit: int = 500) -> str:
        if not value:
            return "Not available"
        compact = " ".join(value.split())
        return compact if len(compact) <= limit else compact[: limit - 1] + "…"

    @staticmethod
    def _source_link(url: str | None) -> str:
        return f"[Open original source]({url})" if url else "Not available"

    @staticmethod
    def _inline_html(value: str) -> str:
        escaped = html.escape(value)
        return re.sub(
            r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
            lambda match: (
                f'<a href="{match.group(2)}" target="_blank" '
                f'rel="noopener noreferrer">{match.group(1)}</a>'
            ),
            escaped,
        )
