# Public-Source Relationship Discovery MVP

This project discovers publicly disclosed related parties, counterparties, and
risk associations. It does not claim complete coverage of undisclosed
customers, suppliers, counterparties, or related parties.

## Runtime baseline

Use the existing Python 3.10 installation. No package installation is required.

```powershell
py -3.10 scripts/init_db.py
py -3.10 -m unittest discover -s tests -v
```

## Retained Rio Tinto preload

Identity ingestion always uses GLEIF as the legal-identity authority and
Wikidata only for aliases and auxiliary identifiers. Official documents are
downloaded, hashed, stored locally, normalized to text, and versioned in
SQLite.

```powershell
# Identity plus one preconfigured official source
py -3.10 scripts/ingest_rio_tinto.py --official companies

# Identity plus every preconfigured Rio Tinto source
py -3.10 scripts/ingest_rio_tinto.py --all-official
```

The retained sample now includes the 2024 and 2025 Annual Reports, the official
companies and transparency pages, and four 2026 official relationship
announcements. PDF text includes stable physical-page markers for later
evidence locators.

This command is a retained preload for the later sample run, not the application
entry point and not a company-specific branch in the generic workflow.

## Generic company news discovery

Set `TAVILY_API_KEY` in the process environment, then provide any company name:

```powershell
py -3.10 scripts/discover_news.py --company "Example Company plc"
```

The service uses six English risk/relationship query groups, starts at 90 days,
and expands to 180 and then 365 days only when fewer than 20 full-text articles
are available. Results are capped at 100, deduplicated by canonical URL and
cleaned content, cached without credentials, and stored locally. Failed public
page retrieval remains `METADATA_ONLY` and is not evidence-capable.

## Generic structured extraction

After setting `GRAPHRAG_API_KEY` (or `OPENAI_API_KEY`), any stored full-text
document version can be extracted:

```powershell
py -3.10 scripts/extract_document.py --document-version-id 123
```

The adapter calls the configured OpenAI-compatible Chat Completions endpoint
with strict JSON Schema output. Documents are split into deterministic,
overlapping chunks. Results are cached by content hash, model, prompt version,
and schema version. The LLM produces candidate facts only; entity resolution,
confidence, validation, and risk severity remain downstream deterministic steps.

## Generic entity resolution

Resolve candidates from any successful extraction run:

```powershell
py -3.10 scripts/resolve_entities.py --extraction-run-id 456
```

Resolution prioritizes exact identifiers, then exact legal names and known
aliases. Character n-gram similarity is used only above a conservative threshold
with a clear margin over the next candidate. Uncertain organizations are stored
as ambiguous group-level entities instead of being forced into a legal entity.
Every decision retains its method, confidence, and matching details.

## Evidence rules and public risk lists

After entity resolution, materialize candidate relations and their exact
evidence excerpts:

```powershell
py -3.10 scripts/materialize_evidence.py --extraction-run-id 456
```

Official documents with an explicit statement receive `HIGH` relationship
confidence. One explicit news source starts at `LOW`; two independent news
publishers describing the same relationship raise it to `MEDIUM`. Inference and
co-mention remain `UNVERIFIED`. Risk severity is calculated separately.

An entity can be checked against the current OFAC, UK Sanctions List, and World
Bank debarment source files:

```powershell
py -3.10 scripts/check_watchlists.py --entity-id 123
```

An exact or similar name alone is only a `POTENTIAL` lead. `CONFIRMED` requires
an exact normalized name plus an independent matching identifier, country, or
address. A watchlist record is never converted into a relationship with the
investigation target.

## English demo interface

Launch the generic company workflow with the existing Streamlit installation:

```powershell
py -3.10 -m streamlit run app.py
```

The five English pages are `Network Explorer`, `New Investigation`,
`Processing Status`, `Investigation Report`, and `Shared Evidence`. When an
investigation exists, the interactive network is the default demo landing
page. A user begins a new case with any company name, confirms the intended
GLEIF legal entity, optionally supplies annual-report or other
official-disclosure URLs, and starts the same end-to-end pipeline. Each step
records its own completion, skip, or failure status.

The dependency-free Network Explorer supports node and edge selection, zoom,
and background panning. Selecting a node opens its structured identity and
investigation-context description. Selecting an edge opens relationship type,
confidence, validation status, dates, description, exact evidence excerpt,
and original source. The graph remains scoped to target one-hop relationships
plus risk-linked limited second-hop paths.

Reports separate validated related parties, counterparties, risk alerts, and
unverified leads. They include an event-ordered timeline, a one-hop NetworkX
view, risk-filtered experimental two-hop paths, evidence excerpts, original
URLs, confidence explanations, and downloadable English Markdown and HTML.

## Versioned presentation descriptions

After extraction and entity resolution, the generic workflow generates concise
English descriptions for every investigation entity and non-co-mention
relationship. These are explicitly marked as AI-generated presentation
summaries: they never replace evidence, establish facts, or affect confidence.

Descriptions are stored in separate versioned tables with the investigation,
model, prompt version, input hash, and supporting assertion IDs. Existing
entities and relationships can therefore be backfilled without re-downloading
or re-extracting documents:

```powershell
py -3.10 scripts/generate_descriptions.py --investigation-id 1
```

Repeated runs reuse matching descriptions. Changed facts or prompts create a
new current version while preserving the previous summary for audit history.

The live demo uses bounded extraction: Tavily may discover and cache up to 100
articles, while only the configured shortlist is sent to the LLM. A real run
through the same generic orchestrator is available for the retained sample:

```powershell
py -3.10 scripts/run_rio_tinto_e2e.py --max-documents 6 --max-news 2
```

Document-level extraction failures are isolated and leave an auditable
`PARTIAL` investigation instead of discarding successful findings. After a
provider or evidence-alignment issue is fixed, retry only failed documents and
regenerate the reports with:

```powershell
py -3.10 scripts/reprocess_investigation.py --investigation-id 1
py -3.10 scripts/refresh_investigation_watchlists.py --investigation-id 1
py -3.10 scripts/audit_investigation.py --investigation-id 1
```

## Reusable Rio Tinto smoke set

Run the deterministic eight-document fixture without invoking Tavily or the
LLM:

```powershell
py -3.10 scripts/smoke_test_rio_tinto.py
```

The fixture includes the retained 2024 annual report, the full 2025 annual
report, companies and transparency pages, and four 2026 official relationship
announcements. It verifies local full text, content hashes, expected source
language, extraction chunking, and incremental reuse. Results are written to
`runtime/reports/rio_tinto_smoke_test.json`; no candidate facts are inserted.

API keys can be supplied through process environment variables or local
project-root `.env` / `local.env` files. Both local files are Git-ignored;
`local.env` overrides `.env`, and process environment variables take final
precedence. Keys are never written to the configuration object representation,
logs, or SQLite database.

## Local data layout

- `runtime/raw`: original public HTML and PDF files
- `runtime/normalized`: cleaned document text
- `runtime/cache`: HTTP and processing cache
- `runtime/database`: the shared SQLite evidence database
- `runtime/reports`: generated English Markdown and HTML reports

The runtime contents are intentionally ignored by Git.
