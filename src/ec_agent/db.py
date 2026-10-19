from __future__ import annotations
import json, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT / "data" / "catalog.sqlite"

def now(): return datetime.now(timezone.utc).isoformat()
def dumps(value): return json.dumps(value, ensure_ascii=False, allow_nan=False)

@contextmanager
def connect(path=DEFAULT_DB):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback(); raise
    finally: con.close()

@contextmanager
def connect_readonly(path=DEFAULT_DB):
    """Analysis tools cannot create a missing database or change catalog data."""
    path = Path(path).resolve(strict=True)
    con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA query_only=ON')
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
    finally:
        con.close()

def initialize(path=DEFAULT_DB):
    with connect(path) as con:
        con.executescript(Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))

def stats(con):
    one = lambda sql: con.execute(sql).fetchone()[0]
    return {
        "entities": one("SELECT count(*) FROM entities"),
        "cas_present": one("SELECT count(*) FROM entities WHERE cas IS NOT NULL AND cas<>''"),
        "structure_records": one("SELECT count(*) FROM entities WHERE selected_structure_id IS NOT NULL"),
        "unique_structures": one("SELECT count(DISTINCT selected_structure_id) FROM entities WHERE selected_structure_id IS NOT NULL"),
        "experiment_candidates": one("SELECT count(*) FROM entities WHERE experiment_candidate=1"),
        "measured": one("SELECT count(*) FROM entities WHERE measured=1"),
        "evidence_entities": one("SELECT count(DISTINCT entity_id) FROM usable_evidence"),
        "evidence_records": one("SELECT count(*) FROM usable_evidence"),
        "evidence_records_archived": one("SELECT count(*) FROM evidence"),
        "evidence_records_withheld": one("SELECT count(*) FROM evidence")-one("SELECT count(*) FROM usable_evidence"),
        "exposure_records": one("SELECT count(*) FROM exposures"),
        "eligible_entities": one("SELECT count(*) FROM entities e JOIN structures s ON s.id=e.selected_structure_id WHERE s.clustering_eligible=1"),
        "categories": [dict(r) for r in con.execute("SELECT category name,count(*) count FROM entities GROUP BY category ORDER BY count(*) DESC")],
        "types": [dict(r) for r in con.execute("SELECT entity_type name,count(*) count FROM entities GROUP BY entity_type ORDER BY count(*) DESC")],
        "cluster_runs": [dict(r) for r in con.execute("SELECT * FROM cluster_runs ORDER BY created_at DESC")],
    }

def filter_values(value, allowed=None):
    """Accept legacy single values and repeated filters; an empty selection means all."""
    values = value if isinstance(value, (list, tuple)) else [value]
    if any(not isinstance(item, str) for item in values):
        raise ValueError('Filter values must be strings')
    values = list(dict.fromkeys(item for item in values if item != ''))
    if allowed is not None and any(item not in allowed for item in values):
        raise ValueError('Invalid filter value')
    return values


def search(con, q="", category="", experiment="", measured="", structure="", run_id="", cluster="", group="", page=1, limit=50):
    clauses=[]; args=[]
    if q:
        clauses.append("(e.id LIKE ? OR e.name LIKE ? OR e.original_name LIKE ? OR e.english_name LIKE ? OR e.cas LIKE ? OR e.id IN (SELECT es.entity_id FROM entity_structures es JOIN structures sx ON sx.id=es.structure_id WHERE sx.inchikey LIKE ?))")
        args.extend(["%"+q+"%"]*6)
    for col,value,allowed in [("category",category,None),("experiment_candidate",experiment,('0','1')),("measured",measured,('0','1'))]:
        values = filter_values(value, allowed)
        if values:
            clauses.append("e."+col+" IN ("+','.join('?' for _ in values)+")")
            args.extend(values)
    structures = filter_values(structure, ('yes','no'))
    if len(structures) == 1:
        clauses.append("e.selected_structure_id IS "+("NOT NULL" if structures[0]=="yes" else "NULL"))
    if run_id and cluster:
        clauses.append("EXISTS(SELECT 1 FROM cluster_members m WHERE m.run_id=? AND m.cluster_id=? AND m.parent_key=s.parent_key)");args.extend([run_id,cluster])
    if group:
        clauses.append("EXISTS(SELECT 1 FROM json_each(s.features_json,'$.functional_groups') j WHERE j.key=?)");args.append(group)
    where=" AND ".join(clauses) or "1=1"
    total=con.execute("SELECT count(*) FROM entities e LEFT JOIN structures s ON s.id=e.selected_structure_id WHERE "+where,args).fetchone()[0]
    limit=max(1,min(int(limit),200));page=max(1,int(page))
    sql="""SELECT e.id,e.name,e.cas,e.english_name,e.category,e.subcategory,e.entity_type,e.measured,
       e.experiment_candidate,e.identity_status,e.human_review,e.issue,s.formula,s.analysis_status,
       s.clustering_eligible,s.exclusion_reason,s.features_json,
       (SELECT count(*) FROM usable_evidence v WHERE v.entity_id=e.id) evidence_count
       FROM entities e LEFT JOIN structures s ON s.id=e.selected_structure_id WHERE """+where+" ORDER BY e.id LIMIT ? OFFSET ?"
    rows=[dict(r) for r in con.execute(sql,args+[limit,(page-1)*limit])]
    for r in rows:
        features=json.loads(r.pop("features_json") or "{}")
        r["functional_groups"]=list(features.get("functional_groups",{}))
    return {"total":total,"page":page,"limit":limit,"rows":rows}

def detail(con, entity_id, evidence_limit=40):
    row=con.execute("SELECT * FROM entities WHERE id=?",(entity_id,)).fetchone()
    if not row: return None
    result=dict(row);result["payload"]=json.loads(result.pop("payload_json"))
    result["structures"]=[dict(r) for r in con.execute("SELECT s.*,es.role,es.cid,es.source_url FROM entity_structures es JOIN structures s ON s.id=es.structure_id WHERE es.entity_id=? ORDER BY es.role",(entity_id,))]
    for r in result["structures"]: r["features"]=json.loads(r.pop("features_json") or "{}")
    from .literature import evidence_page
    evidence=evidence_page(con,entity_id,limit=evidence_limit)
    result['evidence']=evidence['rows'];result['evidence_count']=evidence['total']
    result['evidence_summary']=evidence['summary'];result['evidence_truncated']=evidence['truncated']
    result['withheld_evidence']=evidence['withheld_rows']
    result["exposures"]=[dict(r) for r in con.execute("SELECT * FROM exposures WHERE entity_id=?",(entity_id,))]
    result["clusters"]=[dict(r) for r in con.execute("SELECT m.* FROM cluster_members m JOIN structures s ON s.parent_key=m.parent_key WHERE s.id=?",(result["selected_structure_id"],))]
    result["related_entities"]=[dict(r) for r in con.execute("SELECT id,name,cas FROM entities WHERE selected_structure_id=? AND id<>?",(result["selected_structure_id"],entity_id))] if result["selected_structure_id"] else []
    result["review_history"]=[dict(r) for r in con.execute("SELECT * FROM review_events WHERE entity_id=? ORDER BY id DESC",(entity_id,))]
    return result
