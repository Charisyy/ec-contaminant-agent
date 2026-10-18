PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS imports (
 id TEXT PRIMARY KEY, imported_at TEXT NOT NULL, original_sha256 TEXT NOT NULL,
 snapshot_sha256 TEXT NOT NULL, metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS structures (
 id TEXT PRIMARY KEY, inchikey TEXT NOT NULL, smiles TEXT NOT NULL,
 inchi TEXT, formula TEXT, molecular_weight REAL, features_json TEXT,
 parent_key TEXT, parent_smiles TEXT, clustering_eligible INTEGER DEFAULT 0,
 analysis_status TEXT DEFAULT 'pending', exclusion_reason TEXT
);
CREATE TABLE IF NOT EXISTS entities (
 id TEXT PRIMARY KEY, name TEXT NOT NULL, original_name TEXT NOT NULL, cas TEXT,
 original_cas TEXT, english_name TEXT, category TEXT NOT NULL, subcategory TEXT NOT NULL,
 entity_type TEXT NOT NULL, measured INTEGER NOT NULL CHECK(measured IN (0,1)),
 experiment_candidate INTEGER NOT NULL CHECK(experiment_candidate IN (0,1)),
 procurement_status TEXT NOT NULL DEFAULT 'unknown',
 identity_status TEXT NOT NULL, original_review TEXT, issue TEXT,
 human_review TEXT NOT NULL DEFAULT 'unreviewed', source_databases TEXT,
 source_record_ids TEXT, source_record_count INTEGER NOT NULL, original_row INTEGER NOT NULL,
 selected_structure_id TEXT REFERENCES structures(id), import_id TEXT NOT NULL REFERENCES imports(id),
 payload_json TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS entity_cas ON entities(cas);
CREATE INDEX IF NOT EXISTS entity_category ON entities(category);
CREATE TABLE IF NOT EXISTS entity_structures (
 entity_id TEXT NOT NULL REFERENCES entities(id), structure_id TEXT NOT NULL REFERENCES structures(id),
 role TEXT NOT NULL CHECK(role IN ('selected','candidate','representative_only')),
 cid INTEGER, source_url TEXT, retrieved_at TEXT,
 PRIMARY KEY(entity_id, structure_id, role)
);
CREATE TABLE IF NOT EXISTS sources (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, url TEXT NOT NULL, accessed_at TEXT NOT NULL,
 version TEXT, content_sha256 TEXT, metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
 id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(id), source_id TEXT NOT NULL REFERENCES sources(id),
 evidence_type TEXT NOT NULL CHECK(evidence_type IN ('aquatic_benchmark','PNEC','LC50','EC50','NOEC','other')),
 endpoint TEXT NOT NULL, compartment TEXT NOT NULL, taxon TEXT,
 duration_class TEXT, relation TEXT NOT NULL CHECK(relation IN ('=','<','>','<=','>=')),
 value REAL NOT NULL CHECK(value>0), unit TEXT NOT NULL, source_name TEXT,
 source_year TEXT, source_cas TEXT, chemical_form TEXT,
 original_text TEXT NOT NULL, notes TEXT, identity_confirmed INTEGER NOT NULL DEFAULT 0,
 UNIQUE(entity_id,source_id,endpoint,compartment,taxon,duration_class,relation,value,unit)
);
CREATE TABLE IF NOT EXISTS exposures (
 id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(id), sample_id TEXT NOT NULL,
 compartment TEXT NOT NULL, concentration REAL NOT NULL CHECK(concentration>=0),
 unit TEXT NOT NULL, qualifier TEXT NOT NULL CHECK(qualifier IN ('=','ND','<LOQ')), duration_class TEXT NOT NULL,
 sample_date TEXT, source_url TEXT NOT NULL, identity_confirmed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cluster_runs (
 id TEXT PRIMARY KEY, config_json TEXT NOT NULL, input_digest TEXT NOT NULL,
 created_at TEXT NOT NULL, parent_count INTEGER NOT NULL, cluster_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cluster_members (
 run_id TEXT NOT NULL REFERENCES cluster_runs(id), cluster_id TEXT NOT NULL,
 parent_key TEXT NOT NULL, center_parent_key TEXT NOT NULL, center_similarity REAL NOT NULL,
 PRIMARY KEY(run_id,parent_key)
);
CREATE TABLE IF NOT EXISTS review_events (
 id INTEGER PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(id),
 created_at TEXT NOT NULL, decision TEXT NOT NULL, note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
 result_json TEXT NOT NULL
);
