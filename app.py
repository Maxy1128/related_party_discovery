"""English Streamlit interface for public-source relationship discovery."""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from rpd.config import Settings
from rpd.db import connect, initialize
from rpd.network_explorer import interactive_network_html
from rpd.orchestrator import InvestigationOrchestrator, InvestigationRequest
from rpd.reporting import BOUNDARY_NOTICE, ReportBuilder


st.set_page_config(page_title="Relationship Discovery", page_icon="◈", layout="wide")
st.markdown(
    """<style>
    .block-container {padding-top: 4rem; max-width: 1500px}
    [data-testid="stMetricValue"] {font-size: 1.65rem}
    .boundary {padding: .85rem 1rem; background: #fff8df; border-left: 4px solid #d89b00; color: #3b3423}
    .eyebrow {letter-spacing: .09em; text-transform: uppercase; color: #547185; font-size: .78rem; font-weight: 700}
    </style>""",
    unsafe_allow_html=True,
)

settings = Settings.from_env()
settings.paths.create()
initialize(settings.paths.sqlite_path)


def investigation_options(connection):
    return connection.execute(
        "SELECT id,title,status,created_at FROM investigations ORDER BY id DESC"
    ).fetchall()


def investigation_selector(connection, key: str):
    rows = investigation_options(connection)
    if not rows:
        st.info("No investigation has been created yet.")
        return None
    labels = {row["id"]: f"#{row['id']} · {row['title']} · {row['status']}" for row in rows}
    selected = st.selectbox("Investigation", list(labels), format_func=labels.get, key=key)
    return int(selected)


def network_explorer_page():
    st.markdown('<div class="eyebrow">Network explorer</div>', unsafe_allow_html=True)
    st.title("Explore the relationship network")
    st.markdown(f'<div class="boundary">{BOUNDARY_NOTICE}</div>', unsafe_allow_html=True)
    with connect(settings.paths.sqlite_path) as connection:
        investigation_id = investigation_selector(connection, "network_selector")
        if investigation_id is None:
            st.info("Create an investigation to build its evidence-backed network.")
            return
        builder = ReportBuilder(connection)
        view = builder.load(investigation_id)
        graph = builder.graph(view)
    risk_nodes = sum(bool(data.get("risk")) for _, data in graph.nodes(data=True))
    high_confidence = sum(
        data.get("confidence") in ("HIGH", "MEDIUM")
        for _, _, data in graph.edges(data=True)
    )
    metrics = st.columns(4)
    metrics[0].metric("Entities", graph.number_of_nodes())
    metrics[1].metric("Relationships", graph.number_of_edges())
    metrics[2].metric("High / medium confidence", high_confidence)
    metrics[3].metric("Entities with risk signals", risk_nodes)
    st.caption(
        "Click any node for entity information or any edge for relationship information. "
        "Line weight and pattern encode confidence. Drag nodes to untangle the view, "
        "drag the background to pan, or use Loop hints to inspect directed cycles."
    )
    if graph.nodes:
        components.html(
            interactive_network_html(graph), height=940, scrolling=False,
        )
    else:
        st.info("No evidence-backed network edges are available for this investigation.")
    st.caption(
        "This network shows the investigation target and its one-hop relationships; "
        "second-hop entities appear only when linked to a recorded risk event or watchlist lead."
    )


def new_investigation_page():
    st.markdown('<div class="eyebrow">New investigation</div>', unsafe_allow_html=True)
    st.title("Discover public corporate relationships")
    st.markdown(f'<div class="boundary">{BOUNDARY_NOTICE}</div>', unsafe_allow_html=True)
    st.write("Start with any company name. GLEIF is used to confirm the legal entity before the investigation runs.")
    query = st.text_input("Company name", placeholder="Enter a legal or commonly used company name")
    if st.button("Search legal entities", type="primary", disabled=not query.strip()):
        try:
            with st.spinner("Searching GLEIF…"):
                with connect(settings.paths.sqlite_path) as connection:
                    st.session_state["identity_candidates"] = InvestigationOrchestrator(settings, connection).search_company(query)
                    st.session_state["identity_query"] = query
        except Exception as exc:
            st.error(f"Identity search failed: {type(exc).__name__}: {exc}")
    candidates = st.session_state.get("identity_candidates", [])
    if candidates:
        selected_index = st.selectbox(
            "Confirmed legal entity",
            range(len(candidates)),
            format_func=lambda index: f"{candidates[index].canonical_name} · {candidates[index].lei or 'No LEI'} · {candidates[index].country_code or 'Country unavailable'}",
        )
        selected = candidates[selected_index]
        col1, col2, col3 = st.columns(3)
        col1.metric("LEI", selected.lei or "Unavailable")
        col2.metric("Jurisdiction", selected.country_code or "Unavailable")
        col3.metric("Registration", selected.registration_number or "Unavailable")
        st.caption(selected.registered_address or "Registered address unavailable")
        urls = st.text_area(
            "Official disclosure URLs (optional, one per line)",
            placeholder="Annual report or official transaction announcement URL",
            help="The first-week MVP accepts supplied official URLs for companies other than the preloaded sample.",
        )
        news_col, risk_col = st.columns(2)
        include_news = news_col.checkbox("Search Tavily news", value=True)
        include_watchlists = risk_col.checkbox("Check public risk lists", value=True)
        with st.expander("Demo processing limits"):
            max_documents = st.number_input(
                "Maximum full-text documents sent to the LLM", min_value=1,
                max_value=100, value=8,
            )
            max_news_documents = st.number_input(
                "Maximum news documents within that limit", min_value=0,
                max_value=int(max_documents), value=min(3, int(max_documents)),
            )
        if include_news and not settings.tavily_api_key:
            st.warning("TAVILY_API_KEY is not configured. The news step will be recorded as skipped.")
        if not settings.llm_api_key:
            st.warning("The LLM API key is not configured. Documents can be stored, but extraction will be recorded as skipped.")
        if st.button("Start investigation", type="primary", disabled=not selected.lei):
            progress = st.progress(0, text="Creating investigation…")
            status_box = st.empty()
            step_names = (
                "Identity", "Official documents", "News", "Extraction",
                "Descriptions", "Risk lists", "Report ready",
            )
            step_order = {name: index for index, name in enumerate(step_names, start=1)}

            def update(step, message):
                progress.progress(step_order.get(step, 1) / len(step_names), text=f"{step}: {message}")
                status_box.info(message)

            try:
                request = InvestigationRequest(
                    company_query=st.session_state.get("identity_query", query),
                    selected_lei=selected.lei,
                    official_urls=tuple(url.strip() for url in urls.splitlines() if url.strip()),
                    include_news=include_news,
                    include_watchlists=include_watchlists,
                    max_extraction_documents=int(max_documents),
                    max_news_extraction_documents=int(max_news_documents),
                )
                with connect(settings.paths.sqlite_path) as connection:
                    investigation_id = InvestigationOrchestrator(settings, connection).run(request, update)
                progress.progress(1.0, text="Investigation finished.")
                status_box.success(f"Investigation #{investigation_id} is ready. Open Investigation Report to review it.")
                st.session_state["last_investigation_id"] = investigation_id
            except Exception as exc:
                st.error(f"Investigation failed: {type(exc).__name__}: {exc}")
    elif query:
        st.caption("Search first, then select the intended legal entity.")


def processing_page():
    st.markdown('<div class="eyebrow">Processing status</div>', unsafe_allow_html=True)
    st.title("Investigation progress")
    with connect(settings.paths.sqlite_path) as connection:
        investigation_id = investigation_selector(connection, "processing_selector")
        if investigation_id is None:
            return
        investigation = connection.execute("SELECT * FROM investigations WHERE id=?", (investigation_id,)).fetchone()
        steps = connection.execute("SELECT * FROM investigation_steps WHERE investigation_id=? ORDER BY id", (investigation_id,)).fetchall()
    st.metric("Overall status", investigation["status"])
    for step in steps:
        icon = {"COMPLETED": "✅", "RUNNING": "⏳", "SKIPPED": "➖", "FAILED": "⚠️", "PENDING": "○"}[step["status"]]
        with st.expander(f"{icon} {step['step_name']} · {step['status']}", expanded=step["status"] in ("RUNNING", "FAILED")):
            st.write(step["message"] or "Waiting to start.")
            st.caption(f"Items: {step['item_count']} · Updated: {step['updated_at']}")


def report_page():
    st.markdown('<div class="eyebrow">Investigation report</div>', unsafe_allow_html=True)
    st.title("Evidence-backed findings")
    with connect(settings.paths.sqlite_path) as connection:
        investigation_id = investigation_selector(connection, "report_selector")
        if investigation_id is None:
            return
        builder = ReportBuilder(connection)
        view = builder.load(investigation_id)
        markdown_report = builder.markdown(view)
        html_report = builder.html(view)
        graph = builder.graph(view)
    profile = view.profile
    st.subheader(profile.get("canonical_name") or view.investigation["title"])
    cols = st.columns(4)
    cols[0].metric("Status", view.investigation["status"])
    cols[1].metric("Relationships", len(view.relationships))
    cols[2].metric("Risk events", len(view.risk_events))
    cols[3].metric("Documents", len(view.documents))
    st.caption(f"LEI: {profile.get('lei') or 'Unavailable'} · Country: {profile.get('country_code') or 'Unavailable'} · {profile.get('registered_address') or 'Address unavailable'}")
    if profile.get("entity_description"):
        st.info(profile["entity_description"], icon="✨")
        st.caption("AI-generated presentation summary · Evidence and confidence remain authoritative.")
    tab_related, tab_counterparty, tab_risk, tab_timeline, tab_network, tab_evidence = st.tabs(
        ["Related Parties", "Counterparties", "Risk Alerts", "Timeline", "Network", "Evidence"]
    )
    with tab_related:
        rows = [row for row in view.relationships if row["classification"] == "RELATED_PARTY" and row["validation_status"] == "VALIDATED"]
        st.dataframe(rows, width="stretch", hide_index=True) if rows else st.info("No validated related parties are available.")
    with tab_counterparty:
        rows = [row for row in view.relationships if row["classification"] == "COUNTERPARTY"]
        st.dataframe(rows, width="stretch", hide_index=True) if rows else st.info("No counterparty records are available.")
    with tab_risk:
        if view.risk_events:
            st.dataframe(view.risk_events, width="stretch", hide_index=True)
        if view.watchlist_matches:
            st.dataframe(view.watchlist_matches, width="stretch", hide_index=True)
        if not view.risk_events and not view.watchlist_matches:
            st.info("No risk alerts are available.")
    with tab_timeline:
        rows = [{"date": item.get("timeline_date"), "date_basis": "inferred from publication/retrieval" if item["date_inferred"] else "event date", "kind": item["kind"], "description": item.get("assertion_text") or item.get("description")} for item in view.timeline]
        st.dataframe(rows, width="stretch", hide_index=True) if rows else st.info("No dated findings are available.")
    with tab_network:
        components.html(interactive_network_html(graph), height=940, scrolling=False)
        st.caption("The same interactive explorer is available as a top-level page for faster access.")
    with tab_evidence:
        for relation in view.relationships:
            with st.expander(f"{relation['subject_name']} · {relation['normalized_relation_type']} · {relation['relationship_confidence']}"):
                if relation.get("relationship_description"):
                    st.write(relation["relationship_description"])
                    st.caption("AI-generated presentation summary")
                st.write(relation.get("evidence_text") or "No full-text evidence excerpt available.")
                if relation.get("original_url"):
                    st.link_button("Open original source", relation["original_url"])
    st.markdown(f'<div class="boundary">{BOUNDARY_NOTICE}</div>', unsafe_allow_html=True)
    download_left, download_right = st.columns(2)
    download_left.download_button("Download Markdown report", markdown_report, file_name=f"investigation-{investigation_id}.md", mime="text/markdown", width="stretch")
    download_right.download_button("Download HTML report", html_report, file_name=f"investigation-{investigation_id}.html", mime="text/html", width="stretch")


def shared_evidence_page():
    st.markdown('<div class="eyebrow">Shared evidence</div>', unsafe_allow_html=True)
    st.title("Reusable public-source records")
    with connect(settings.paths.sqlite_path) as connection:
        counts = {
            "Entities": connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
            "Documents": connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "Document versions": connection.execute("SELECT COUNT(*) FROM document_versions").fetchone()[0],
            "Evidence excerpts": connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0],
        }
        entities = [dict(row) for row in connection.execute(
            """SELECT e.id,e.canonical_name,e.entity_scope,e.lei,e.country_code,e.ambiguous,
                      e.updated_at,(SELECT ed.description FROM entity_descriptions ed
                        WHERE ed.entity_id=e.id AND ed.is_current=1
                        ORDER BY ed.created_at DESC LIMIT 1) description,
                      (SELECT ed.investigation_id FROM entity_descriptions ed
                        WHERE ed.entity_id=e.id AND ed.is_current=1
                        ORDER BY ed.created_at DESC LIMIT 1) description_investigation_id
               FROM entities e ORDER BY e.updated_at DESC LIMIT 200"""
        )]
        documents = [dict(row) for row in connection.execute("""SELECT d.id,d.title,d.source_type,d.publisher,d.original_url,d.published_at,v.retrieval_status,v.retrieved_at FROM documents d LEFT JOIN document_versions v ON v.document_id=d.id AND v.is_current=1 ORDER BY v.retrieved_at DESC LIMIT 200""")]
    columns = st.columns(4)
    for column, (label, value) in zip(columns, counts.items()):
        column.metric(label, value)
    entity_tab, document_tab = st.tabs(["Entities", "Documents"])
    with entity_tab:
        st.dataframe(entities, width="stretch", hide_index=True)
    with document_tab:
        st.dataframe(documents, width="stretch", hide_index=True, column_config={"original_url": st.column_config.LinkColumn("Original URL")})
    st.caption("Full document text remains on this computer. Reports expose only necessary evidence excerpts and original links.")


st.sidebar.title("Relationship Discovery")
with connect(settings.paths.sqlite_path) as connection:
    has_investigations = bool(investigation_options(connection))
pages = (
    "Network Explorer", "New Investigation", "Processing Status",
    "Investigation Report", "Shared Evidence",
)
page = st.sidebar.radio("Navigate", pages, index=0 if has_investigations else 1)
st.sidebar.caption("Public sources · traceable evidence · explicit uncertainty")
if page == "Network Explorer":
    network_explorer_page()
elif page == "New Investigation":
    new_investigation_page()
elif page == "Processing Status":
    processing_page()
elif page == "Investigation Report":
    report_page()
else:
    shared_evidence_page()
