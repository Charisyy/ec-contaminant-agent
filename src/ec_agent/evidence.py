from __future__ import annotations
import csv,hashlib,json,re,math
from pathlib import Path
from .db import connect,dumps,now

BENCHMARK_URL='https://www.epa.gov/pesticide-science-and-assessing-pesticide-risks/aquatic-life-benchmarks-and-ecological-risk'
BENCHMARK_COLUMNS=[
 ('freshwater','淡水脊椎动物','acute'),('freshwater','淡水脊椎动物','chronic'),
 ('marine','河口/海水脊椎动物','acute'),('marine','河口/海水脊椎动物','chronic'),
 ('freshwater','淡水无脊椎动物','acute'),('freshwater','淡水无脊椎动物','chronic'),
 ('marine','河口/海水无脊椎动物','acute'),('marine','河口/海水无脊椎动物','chronic'),
 ('freshwater','非维管植物','IC50'),('freshwater','非维管植物','NOAEC'),
 ('freshwater','维管植物','IC50'),('freshwater','维管植物','NOAEC'),
]

def numeric_cell(text):
    m=re.match(r'^\s*([<>]=?|≤|≥)?\s*([\d,]+(?:\.\d+)?(?:[eE][+-]?\d+)?)',text)
    if not m: return None
    value=float(m[2].replace(',',''))
    if not math.isfinite(value) or value<=0: return None
    relation={'≤':'<=','≥':'>='}.get(m[1],m[1] or '=')
    return relation,value

def import_epa(snapshot,db):
    source=json.loads(Path(snapshot).read_text(encoding='utf-8')); linked=0;entities=set()
    with connect(db) as con:
        con.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?,?)',('epa-alb-2025-09-04','aquatic_benchmark',BENCHMARK_URL,source['accessed_at'],'2025-09-04',source['html_sha256'],dumps({'unit':'ug/L','scope':'EPA OPP aquatic life benchmarks; separate taxa and compartments','not_PNEC':True})))
        for record in source['rows']:
            cells=record['cells']
            if len(cells)!=17: raise ValueError(f'Unexpected EPA table width: {len(cells)}')
            cas=cells[2].strip()
            if not re.fullmatch(r'\d{2,7}-\d{2}-\d',cas): continue
            targets=con.execute('SELECT id FROM entities WHERE cas=?',(cas,)).fetchall()
            for target in targets:
                for i,(compartment,taxon,duration) in enumerate(BENCHMARK_COLUMNS):
                    text=cells[i+3]; parsed=numeric_cell(text)
                    if not parsed: continue
                    relation,value=parsed; eid=target['id']
                    endpoint=taxon+' / '+duration+' / EPA aquatic life benchmark'
                    evid=hashlib.sha256(dumps([eid,cas,endpoint,text,cells[1]]).encode()).hexdigest()[:24]
                    con.execute('''INSERT OR IGNORE INTO evidence(id,entity_id,source_id,evidence_type,endpoint,compartment,taxon,duration_class,relation,value,unit,source_name,source_year,source_cas,chemical_form,original_text,notes,identity_confirmed)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (evid,eid,'epa-alb-2025-09-04','aquatic_benchmark',endpoint,compartment,taxon,{'IC50':'acute','NOAEC':'chronic'}.get(duration,duration),relation,value,'ug/L',cells[0],cells[1],cas,'exact CAS mapping',text,dumps({'assessment_links':record['links'],'source_footnotes_retained':True,'notice':'基准值，不是LC50/EC50/PNEC；限定符与原文保留；用于筛查，非当地法规标准'}),1))
                    linked+=1;entities.add(eid)
    return {'evidence_rows_matched':linked,'entities_matched':len(entities),'source_version':'2025-09-04'}

UNIT_FACTOR={'ug/L':1.,'µg/L':1.,'μg/L':1.,'mg/L':1000.,'ng/L':.001}
def to_ug_l(value,unit):
    if unit not in UNIT_FACTOR: raise ValueError('Unsupported water concentration unit: '+unit)
    if not math.isfinite(float(value)) or float(value)<0: raise ValueError('Concentration must be finite and nonnegative')
    return float(value)*UNIT_FACTOR[unit]

def import_exposures(csv_path,db):
    rows=list(csv.DictReader(Path(csv_path).open(encoding='utf-8-sig',newline='')))
    required={'entity_id','sample_id','compartment','concentration','unit','qualifier','duration_class','sample_date','source_url','identity_confirmed'}
    if rows and not required.issubset(rows[0]): raise ValueError('Missing exposure columns: '+','.join(sorted(required-set(rows[0]))))
    with connect(db) as con:
        for r in rows:
            if r['compartment'] not in ('freshwater','marine','soil','sediment'): raise ValueError('Explicit compartment required')
            if not r['source_url'].strip(): raise ValueError('Exposure source is required')
            if r['duration_class'] not in ('acute','chronic','unknown'): raise ValueError('Exposure duration_class must be acute, chronic or unknown')
            value=float(r['concentration']);to_ug_l(value,r['unit'])
            ident=hashlib.sha256(dumps(r).encode()).hexdigest()[:24]
            con.execute('INSERT OR REPLACE INTO exposures VALUES(?,?,?,?,?,?,?,?,?,?,?)',(ident,r['entity_id'],r['sample_id'],r['compartment'],value,r['unit'],r['qualifier'],r['duration_class'],r['sample_date'],r['source_url'],int(r['identity_confirmed']=='1')))
    return {'imported':len(rows)}

def import_evidence(csv_path,db):
    file=Path(csv_path);rows=list(csv.DictReader(file.open(encoding='utf-8-sig',newline='')))
    source_id='manual-'+hashlib.sha256(file.read_bytes()).hexdigest()[:20]
    required={'entity_id','evidence_type','endpoint','compartment','taxon','duration_class','relation','value','unit','source_url','source_year','identity_confirmed'}
    if rows and not required.issubset(rows[0]):raise ValueError('Missing evidence columns: '+','.join(sorted(required-set(rows[0]))))
    with connect(db) as con:
        for i,r in enumerate(rows,1):
            if r['evidence_type'] not in ('aquatic_benchmark','PNEC','LC50','EC50','NOEC','other'):raise ValueError('Unknown evidence type')
            if r['duration_class'] not in ('acute','chronic','unknown'):raise ValueError('Explicit duration class required')
            if not r['source_url'].strip():raise ValueError('Evidence source required')
            value=float(r['value'])
            if not math.isfinite(value) or value<=0:raise ValueError('Evidence value must be finite and positive')
            sid=source_id+'-'+str(i)
            con.execute('INSERT OR IGNORE INTO sources VALUES(?,?,?,?,?,?,?)',(sid,r['evidence_type'],r['source_url'],now(),r['source_year'],hashlib.sha256(file.read_bytes()).hexdigest(),dumps({'input':file.name,'row':i+1})))
            con.execute('''INSERT OR IGNORE INTO evidence(id,entity_id,source_id,evidence_type,endpoint,compartment,taxon,duration_class,relation,value,unit,source_name,source_year,original_text,notes,identity_confirmed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
             (sid,r['entity_id'],sid,r['evidence_type'],r['endpoint'],r['compartment'],r['taxon'],r['duration_class'],r['relation'],value,r['unit'],r.get('source_name','manual'),r['source_year'],r['relation']+r['value'],r.get('notes',''),int(r['identity_confirmed']=='1')))
    return {'imported':len(rows)}

def risk_results(con,entity_id):
    entity=con.execute('SELECT human_review FROM entities WHERE id=?',(entity_id,)).fetchone()
    if not entity: return {'status':'unknown_entity','ratios':[],'reason':'实体不存在'}
    if entity[0]!='confirmed': return {'status':'identity_review_required','ratios':[],'reason':'尚未人工确认化学身份；不输出定量风险结论'}
    exposures=con.execute('SELECT * FROM exposures WHERE entity_id=?',(entity_id,)).fetchall()
    if not exposures: return {'status':'exposure_missing','ratios':[],'reason':'缺少实测暴露浓度；未知不等于低风险'}
    evidence=con.execute("SELECT * FROM usable_evidence WHERE entity_id=? AND evidence_type IN ('PNEC','aquatic_benchmark')",(entity_id,)).fetchall()
    ratios=[];blocked=[]
    for x in exposures:
        if not x['identity_confirmed'] or x['qualifier']!='=' or x['duration_class']=='unknown':
            blocked.append('暴露身份未确认或为未检出/低于定量限，未替代为零');continue
        for v in evidence:
            if v['compartment']!=x['compartment'] or v['duration_class']!=x['duration_class'] or not v['identity_confirmed'] or v['relation']!='=': continue
            # Footnoted qualifications (e.g. EPA footnote G) require a human interpretation.
            suffix=re.sub(r'^\s*([<>]=?|≤|≥)?\s*[\d,]+(?:\.\d+)?(?:[eE][+-]?\d+)?','',v['original_text'])
            if v['evidence_type']=='aquatic_benchmark' and re.search(r'[A-Za-z]',suffix):
                blocked.append('基准附有限定脚注，待人工解释');continue
            try: ratio=to_ug_l(x['concentration'],x['unit'])/to_ug_l(v['value'],v['unit'])
            except ValueError: continue
            ratios.append({'sample_id':x['sample_id'],'exposure_id':x['id'],'evidence_id':v['id'],'kind':'RQ_PNEC' if v['evidence_type']=='PNEC' else 'benchmark_screening_quotient',
               'ratio':ratio,'endpoint':v['endpoint'],'compartment':v['compartment'],
               'interpretation':'达到或超过该基准，需进一步核查' if ratio>=1 else '低于本条基准；不代表其他端点或环境总体安全'})
    return {'status':'calculated' if ratios else 'no_comparable_evidence','ratios':ratios,'reason':'各端点单独计算；未混合急性、慢性或不同介质','blocked':sorted(set(blocked))}

def first_pass(con):
    report=[]
    for e in con.execute('SELECT e.*,s.analysis_status,s.clustering_eligible FROM entities e LEFT JOIN structures s ON s.id=e.selected_structure_id ORDER BY e.id'):
        evidence=con.execute('SELECT count(*) FROM usable_evidence WHERE entity_id=?',(e['id'],)).fetchone()[0]
        exposure=con.execute('SELECT count(*) FROM exposures WHERE entity_id=?',(e['id'],)).fetchone()[0]
        if e['entity_type']!='单体/命名实体': action='材料/混合物专项评价'
        elif not e['selected_structure_id']: action='补结构或确认多候选'
        elif e['human_review']!='confirmed': action='核对身份并补暴露' if evidence else '核对身份并补生态证据'
        elif not evidence: action='补生态毒性证据'
        elif not exposure: action='补环境暴露数据'
        else: action='核查可比较端点'
        report.append({'entity_id':e['id'],'name':e['name'],'category':e['category'],'action':action,'evidence_count':evidence,'exposure_count':exposure,'risk_status':risk_results(con,e['id'])['status'],'risk_score':None})
    return report
