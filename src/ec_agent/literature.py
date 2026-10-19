"""Conservative, reproducible import of sourced experimental endpoints; no prediction."""
from __future__ import annotations
import gzip,hashlib,json,math,re
from pathlib import Path
from .db import connect,dumps

PACK_FORMAT='ec-literature-v1'
ALLOWED_FORMS={'','--','NR','NC','PU','AI','ARST','CP','CRY','AQ'}
ALLOWED_BASIS={'','--','NR','NC','A','D','T'}
UNITS={'mg/L','ug/L','ng/L'}

def cas_digits(value):
    value=value.strip().replace('-','')
    if not re.fullmatch(r'\d{5,10}',value):return None
    if sum(int(n)*i for i,n in enumerate(reversed(value[:-1]),1))%10!=int(value[-1]):return None
    return value

def select_result(test,result):
    """Return an exclusion reason or None. Slash/asterisk flags require separate review."""
    if test['media_type'] not in {'FW','SW'}:return 'medium_not_unqualified_FW_SW'
    if result['endpoint'] not in {'LC50','EC50','NOEC'}:return 'endpoint_not_unqualified_LC50_EC50_NOEC'
    if test['test_formulation'] not in ALLOWED_FORMS:return 'formulation_needs_review'
    if result['conc1_type'] not in ALLOWED_BASIS:return 'concentration_basis_needs_review'
    if result['ion1'] not in {'','--','NR','NC'}:return 'ion_equivalent_needs_review'
    if result['conc1_unit'] not in UNITS:return 'unit_needs_review'
    if result['conc1_mean_op'] not in {'','=', '<','>','<=','>='}:return 'qualifier_needs_review'
    try:value=float(result['conc1_mean'])
    except (ValueError,TypeError):return 'numeric_mean_missing'
    if not math.isfinite(value) or value<=0:return 'numeric_mean_invalid'
    return None

def evidence_summary(con,entity_id):
    counts={r[0]:r[1] for r in con.execute('SELECT evidence_type,count(*) FROM evidence WHERE entity_id=? GROUP BY evidence_type',(entity_id,))}
    return {'total':sum(counts.values()),'by_type':counts,
        'literature_references':con.execute("SELECT count(DISTINCT source_id) FROM evidence WHERE entity_id=? AND evidence_type IN ('LC50','EC50','NOEC')",(entity_id,)).fetchone()[0],
        'notice':'实验端点、评价基准分别保留；数据库整理记录尚未逐篇核对原文，CAS精确匹配不等于人工确认身份。'}

def evidence_page(con,entity_id,page=1,limit=40):
    page=max(1,int(page));limit=max(1,min(int(limit),200));summary=evidence_summary(con,entity_id)
    rows=[dict(r) for r in con.execute('''SELECT v.*,s.url source_url,s.version source_version,s.accessed_at,s.content_sha256,
        s.metadata_json source_metadata_json FROM evidence v JOIN sources s ON s.id=v.source_id WHERE v.entity_id=?
        ORDER BY CASE WHEN s.kind='literature' THEN 0 ELSE 1 END,v.source_year DESC,v.source_id,v.id LIMIT ? OFFSET ?''',(entity_id,limit,(page-1)*limit))]
    for row in rows:
        metadata=json.loads(row.pop('source_metadata_json'));notes=json.loads(row['notes']) if row['notes'] and row['notes'].startswith('{') else {}
        row['reference']=metadata.get('reference',{})
        row['verification_level']=notes.get('verification_level',metadata.get('verification_level','regulatory_compilation'))
        row['observed_duration']=notes.get('observed_duration',{})
        row['concentration_basis']=notes.get('concentration_basis',{})
    return {'rows':rows,'page':page,'limit':limit,'total':summary['total'],'truncated':page*limit<summary['total'],'summary':summary}

def import_pack(path,db):
    """Atomic additive import. Existing records must match; catalog/reviews never change."""
    path=Path(path);digest=hashlib.sha256(path.read_bytes()).hexdigest();added=0;seen=set();sources=set()
    with gzip.open(path,'rt',encoding='utf-8') as f,connect(db) as con:
        header=json.loads(next(f))
        if header.get('format')!=PACK_FORMAT:raise ValueError('Unknown evidence pack format')
        for line in f:
            row=json.loads(line);source=row.pop('source');sid=source['id'];eid=row['entity_id']
            entity=con.execute('SELECT cas,entity_type FROM entities WHERE id=?',(eid,)).fetchone()
            if not entity or entity['entity_type']!='单体/命名实体':raise ValueError('Evidence requires an exact single-entity mapping')
            cas=cas_digits(row['source_cas'])
            if not cas or cas!=cas_digits(entity['cas'] or ''):raise ValueError('Evidence CAS does not match catalog')
            if row['id'] in seen:raise ValueError('Duplicate evidence ID inside pack')
            seen.add(row['id']);sources.add(sid)
            if row['evidence_type'] not in {'LC50','EC50','NOEC'} or row['unit'] not in UNITS:raise ValueError('Unsupported experimental endpoint/unit')
            if row['compartment'] not in {'freshwater','marine'} or row['duration_class'] not in {'acute','chronic','unknown'}:raise ValueError('Explicit water compartment/duration required')
            if not math.isfinite(row['value']) or row['value']<=0 or row['relation'] not in {'=','<','>','<=','>='}:raise ValueError('Invalid concentration')
            if row['identity_confirmed']!=0:raise ValueError('CAS mapping cannot confirm identity')
            if not source['url'].startswith('https://'):raise ValueError('Traceable source URL required')
            values=(sid,source['kind'],source['url'],source['accessed_at'],source['version'],source['content_sha256'],dumps(source['metadata']))
            previous=con.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone()
            if previous and tuple(previous)!=values:raise ValueError('Existing source differs; explicit review required')
            con.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?)',values)
            fields=['id','entity_id','source_id','evidence_type','endpoint','compartment','taxon','duration_class','relation','value','unit','source_name','source_year','source_cas','chemical_form','original_text','notes','identity_confirmed']
            row['source_id']=sid
            values=tuple(row[k] for k in fields)
            previous=con.execute('SELECT '+','.join(fields)+' FROM evidence WHERE id=?',(row['id'],)).fetchone()
            if previous and tuple(previous)!=values:raise ValueError('Existing evidence differs; explicit review required')
            cur=con.execute('INSERT OR IGNORE INTO evidence('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',values)
            if not previous and not cur.rowcount:raise ValueError('Evidence uniqueness collision')
            added+=cur.rowcount
        if len(seen)!=header['records']:raise ValueError('Evidence pack count mismatch')
    return {'added':added,'pack_records':len(seen),'references':len(sources),'sha256':digest}
