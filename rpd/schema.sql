PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS investigations (
    id INTEGER PRIMARY KEY,
    target_entity_id INTEGER REFERENCES entities(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'CREATED'
        CHECK (status IN ('CREATED', 'RUNNING', 'COMPLETED', 'PARTIAL', 'FAILED')),
    parameters_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    legal_name TEXT,
    entity_scope TEXT NOT NULL DEFAULT 'LEGAL_ENTITY'
        CHECK (entity_scope IN ('LEGAL_ENTITY', 'GROUP', 'PERSON', 'GOVERNMENT', 'OTHER')),
    entity_type TEXT,
    lei TEXT UNIQUE,
    registration_number TEXT,
    registration_authority TEXT,
    country_code TEXT,
    registered_address TEXT,
    website TEXT,
    ambiguous INTEGER NOT NULL DEFAULT 0 CHECK (ambiguous IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_entities_normalized_name
    ON entities(normalized_name);
CREATE INDEX IF NOT EXISTS idx_entities_registration
    ON entities(registration_authority, registration_number);

CREATE TABLE IF NOT EXISTS entity_aliases (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (entity_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_entity_aliases_normalized
    ON entity_aliases(normalized_alias);

CREATE TABLE IF NOT EXISTS entity_identifiers (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    identifier_scheme TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (entity_id, identifier_scheme, identifier_value)
);

CREATE INDEX IF NOT EXISTS idx_entity_identifiers_lookup
    ON entity_identifiers(identifier_scheme, identifier_value);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    source_type TEXT NOT NULL,
    title TEXT,
    publisher TEXT,
    original_url TEXT,
    normalized_url TEXT,
    language TEXT NOT NULL DEFAULT 'en',
    published_at TEXT,
    first_retrieved_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (source_type, normalized_url)
);

CREATE INDEX IF NOT EXISTS idx_documents_published_at ON documents(published_at);

CREATE TABLE IF NOT EXISTS news_search_results (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    query TEXT NOT NULL,
    search_days INTEGER NOT NULL CHECK (search_days IN (90, 180, 365)),
    tavily_score REAL NOT NULL DEFAULT 0.0,
    full_text_source TEXT NOT NULL CHECK (
        full_text_source IN ('TAVILY', 'LOCAL_FALLBACK', 'METADATA_ONLY')
    ),
    discovered_at TEXT NOT NULL,
    UNIQUE (document_id, query, search_days)
);

CREATE INDEX IF NOT EXISTS idx_news_search_results_document
    ON news_search_results(document_id);

CREATE TABLE IF NOT EXISTS document_versions (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content_hash TEXT NOT NULL,
    raw_content_hash TEXT,
    media_type TEXT,
    byte_size INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
    raw_path TEXT,
    normalized_path TEXT,
    retrieval_status TEXT NOT NULL DEFAULT 'FULL_TEXT'
        CHECK (retrieval_status IN ('FULL_TEXT', 'METADATA_ONLY', 'FAILED')),
    retrieved_at TEXT NOT NULL,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (document_id, content_hash)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_document_versions_one_current
    ON document_versions(document_id) WHERE is_current = 1;
CREATE INDEX IF NOT EXISTS idx_document_versions_hash
    ON document_versions(content_hash);

CREATE TABLE IF NOT EXISTS extraction_runs (
    id INTEGER PRIMARY KEY,
    document_version_id INTEGER NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')),
    response_json TEXT,
    error_message TEXT,
    cache_source_run_id INTEGER REFERENCES extraction_runs(id),
    started_at TEXT,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (document_version_id, model, prompt_version, schema_version)
);

CREATE TABLE IF NOT EXISTS extraction_chunks (
    id INTEGER PRIMARY KEY,
    extraction_run_id INTEGER NOT NULL REFERENCES extraction_runs(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset >= start_offset),
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('SUCCEEDED', 'FAILED')),
    response_json TEXT,
    cache_source_chunk_id INTEGER REFERENCES extraction_chunks(id),
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (extraction_run_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_extraction_chunks_cache
    ON extraction_chunks(content_hash, status);

CREATE TABLE IF NOT EXISTS mentions (
    id INTEGER PRIMARY KEY,
    extraction_run_id INTEGER NOT NULL REFERENCES extraction_runs(id) ON DELETE CASCADE,
    extraction_chunk_id INTEGER REFERENCES extraction_chunks(id) ON DELETE CASCADE,
    candidate_local_id TEXT,
    entity_id INTEGER REFERENCES entities(id),
    mention_text TEXT NOT NULL,
    normalized_mention TEXT NOT NULL,
    mention_type TEXT,
    start_offset INTEGER,
    end_offset INTEGER,
    context_text TEXT,
    resolution_status TEXT NOT NULL DEFAULT 'UNRESOLVED'
        CHECK (resolution_status IN ('RESOLVED', 'GROUP_LEVEL', 'AMBIGUOUS', 'UNRESOLVED')),
    resolution_confidence REAL CHECK (
        resolution_confidence IS NULL OR
        (resolution_confidence >= 0.0 AND resolution_confidence <= 1.0)
    ),
    resolution_method TEXT,
    resolution_details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (start_offset IS NULL OR start_offset >= 0),
    CHECK (end_offset IS NULL OR start_offset IS NULL OR end_offset >= start_offset)
);

CREATE INDEX IF NOT EXISTS idx_mentions_entity ON mentions(entity_id);
CREATE INDEX IF NOT EXISTS idx_mentions_normalized ON mentions(normalized_mention);

CREATE TABLE IF NOT EXISTS extracted_entity_candidates (
    id INTEGER PRIMARY KEY,
    extraction_chunk_id INTEGER NOT NULL REFERENCES extraction_chunks(id) ON DELETE CASCADE,
    local_id TEXT NOT NULL,
    candidate_json TEXT NOT NULL,
    resolved_entity_id INTEGER NOT NULL REFERENCES entities(id),
    resolution_status TEXT NOT NULL CHECK (
        resolution_status IN ('RESOLVED', 'GROUP_LEVEL', 'AMBIGUOUS', 'UNRESOLVED')
    ),
    resolution_method TEXT NOT NULL,
    resolution_confidence REAL NOT NULL CHECK (
        resolution_confidence >= 0.0 AND resolution_confidence <= 1.0
    ),
    resolution_details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (extraction_chunk_id, local_id)
);

CREATE INDEX IF NOT EXISTS idx_extracted_candidates_entity
    ON extracted_entity_candidates(resolved_entity_id);

CREATE TABLE IF NOT EXISTS relation_type_registry (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE,
    definition TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    subject_role TEXT NOT NULL,
    object_role TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('DIRECTED', 'UNDIRECTED')),
    relation_family TEXT NOT NULL CHECK (
        relation_family IN (
            'CORPORATE_STRUCTURE', 'MANAGEMENT_OWNERSHIP',
            'COMMERCIAL', 'REGULATORY_RISK', 'OTHER'
        )
    ),
    registry_status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (registry_status IN ('ACTIVE', 'PROPOSED', 'REJECTED')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS assertions (
    id INTEGER PRIMARY KEY,
    extraction_run_id INTEGER REFERENCES extraction_runs(id) ON DELETE SET NULL,
    subject_entity_id INTEGER NOT NULL REFERENCES entities(id),
    object_entity_id INTEGER REFERENCES entities(id),
    relation_type_id INTEGER REFERENCES relation_type_registry(id),
    normalized_relation_type TEXT NOT NULL,
    proposed_relation_type TEXT,
    classification TEXT NOT NULL CHECK (
        classification IN ('RELATED_PARTY', 'COUNTERPARTY', 'RISK_RELATION', 'CO_MENTION')
    ),
    assertion_text TEXT NOT NULL,
    explicit_or_inferred TEXT NOT NULL
        CHECK (explicit_or_inferred IN ('EXPLICIT', 'INFERRED')),
    validation_status TEXT NOT NULL DEFAULT 'CANDIDATE'
        CHECK (validation_status IN ('CANDIDATE', 'VALIDATED', 'REJECTED')),
    relationship_confidence TEXT NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (relationship_confidence IN ('HIGH', 'MEDIUM', 'LOW', 'UNVERIFIED')),
    event_date TEXT,
    valid_from TEXT,
    valid_to TEXT,
    published_at TEXT,
    retrieved_at TEXT,
    ambiguity_flags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (subject_entity_id != object_entity_id OR classification = 'CO_MENTION')
);

CREATE INDEX IF NOT EXISTS idx_assertions_subject ON assertions(subject_entity_id);
CREATE INDEX IF NOT EXISTS idx_assertions_object ON assertions(object_entity_id);
CREATE INDEX IF NOT EXISTS idx_assertions_timeline
    ON assertions(event_date, published_at, retrieved_at);
CREATE INDEX IF NOT EXISTS idx_assertions_classification
    ON assertions(classification, relationship_confidence);

CREATE TABLE IF NOT EXISTS evidence (
    id INTEGER PRIMARY KEY,
    assertion_id INTEGER REFERENCES assertions(id) ON DELETE CASCADE,
    document_version_id INTEGER NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
    evidence_text TEXT NOT NULL,
    locator_json TEXT NOT NULL DEFAULT '{}',
    evidence_kind TEXT NOT NULL DEFAULT 'FULL_TEXT'
        CHECK (evidence_kind IN ('FULL_TEXT', 'METADATA_ONLY')),
    evidence_quality TEXT NOT NULL DEFAULT 'EXACT'
        CHECK (evidence_quality IN ('EXACT', 'PARAPHRASED')),
    supports_assertion INTEGER NOT NULL DEFAULT 1 CHECK (supports_assertion IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (length(trim(evidence_text)) > 0)
);

CREATE INDEX IF NOT EXISTS idx_evidence_assertion ON evidence(assertion_id);
CREATE INDEX IF NOT EXISTS idx_evidence_document_version ON evidence(document_version_id);

CREATE TABLE IF NOT EXISTS risk_events (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    assertion_id INTEGER REFERENCES assertions(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    description TEXT NOT NULL,
    risk_severity TEXT NOT NULL CHECK (
        risk_severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFORMATIONAL')
    ),
    event_date TEXT,
    published_at TEXT,
    retrieved_at TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_risk_events_entity ON risk_events(entity_id);
CREATE INDEX IF NOT EXISTS idx_risk_events_timeline
    ON risk_events(event_date, published_at, retrieved_at);

CREATE TABLE IF NOT EXISTS watchlist_matches (
    id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    list_name TEXT NOT NULL CHECK (list_name IN ('OFAC', 'UK_SANCTIONS', 'WORLD_BANK')),
    list_record_id TEXT,
    matched_name TEXT NOT NULL,
    match_method TEXT NOT NULL,
    match_score REAL CHECK (match_score IS NULL OR (match_score >= 0.0 AND match_score <= 1.0)),
    match_status TEXT NOT NULL DEFAULT 'POTENTIAL'
        CHECK (match_status IN ('POTENTIAL', 'CONFIRMED', 'REJECTED')),
    rationale TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_retrieved_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (entity_id, list_name, list_record_id)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_matches_entity
    ON watchlist_matches(entity_id, match_status);

CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('ENTITY', 'MENTION', 'EVIDENCE')),
    owner_id INTEGER NOT NULL,
    model TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK (dimensions > 0),
    vector_blob BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (owner_type, owner_id, model, content_hash)
);

CREATE TABLE IF NOT EXISTS investigation_documents (
    investigation_id INTEGER NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (investigation_id, document_id)
);

CREATE TABLE IF NOT EXISTS investigation_assertions (
    investigation_id INTEGER NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    assertion_id INTEGER NOT NULL REFERENCES assertions(id) ON DELETE CASCADE,
    added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (investigation_id, assertion_id)
);

CREATE TABLE IF NOT EXISTS investigation_steps (
    id INTEGER PRIMARY KEY,
    investigation_id INTEGER NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    step_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'SKIPPED', 'FAILED')),
    item_count INTEGER NOT NULL DEFAULT 0 CHECK (item_count >= 0),
    message TEXT,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (investigation_id, step_name)
);

CREATE INDEX IF NOT EXISTS idx_investigation_steps_status
    ON investigation_steps(investigation_id, status);

CREATE TABLE IF NOT EXISTS entity_descriptions (
    id INTEGER PRIMARY KEY,
    investigation_id INTEGER NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    description TEXT NOT NULL CHECK (length(trim(description)) > 0),
    generation_method TEXT NOT NULL CHECK (generation_method IN ('LLM_GENERATED', 'TEMPLATE')),
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    source_assertion_ids_json TEXT NOT NULL DEFAULT '[]',
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (investigation_id, entity_id, model, prompt_version, input_hash)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entity_descriptions_current
    ON entity_descriptions(investigation_id, entity_id) WHERE is_current=1;

CREATE TABLE IF NOT EXISTS relationship_descriptions (
    id INTEGER PRIMARY KEY,
    investigation_id INTEGER NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    assertion_id INTEGER NOT NULL REFERENCES assertions(id) ON DELETE CASCADE,
    description TEXT NOT NULL CHECK (length(trim(description)) > 0),
    generation_method TEXT NOT NULL CHECK (generation_method IN ('LLM_GENERATED', 'TEMPLATE')),
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    source_assertion_ids_json TEXT NOT NULL DEFAULT '[]',
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (investigation_id, assertion_id, model, prompt_version, input_hash)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_relationship_descriptions_current
    ON relationship_descriptions(investigation_id, assertion_id) WHERE is_current=1;
