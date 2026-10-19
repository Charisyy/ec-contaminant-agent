import copy,gzip,json,sqlite3,shutil
import pytest
from ec_agent.db import DEFAULT_DB,PROJECT,connect,connect_readonly,detail
from ec_agent.literature import select_result,cas_digits,import_pack,evidence_page

def test_cas_checksum_and_no_form_equivalence():
    assert cas_digits('80-05-7')=='80057'
    assert cas_digits('80-05-8') is None
    assert cas_digits('335-67-1')!=cas_digits('3825-26-1')

@pytest.mark.parametrize('field,value,reason',[
    ('media_type','FW/','medium_not_unqualified_FW_SW'),('test_formulation','EC','formulation_needs_review'),
    ('endpoint','LC50*','endpoint_not_unqualified_LC50_EC50_NOEC'),('conc1_type','F','concentration_basis_needs_review'),
    ('ion1','Hg','ion_equivalent_needs_review'),('conc1_unit','mg/kg','unit_needs_review'),
    ('conc1_mean_op','~','qualifier_needs_review'),('conc1_mean','nan','numeric_mean_invalid')])
def test_conservative_selection(field,value,reason):
    t={'media_type':'FW','test_formulation':'PU'}
    r={'endpoint':'LC50','conc1_type':'A','ion1':'NR','conc1_unit':'ug/L','conc1_mean_op':'>','conc1_mean':'1'}
    assert select_result(t,r) is None
    (t if field in t else r)[field]=value
    assert select_result(t,r)==reason

def write_pack(path,rows):
    with gzip.open(path,'wt',encoding='utf-8') as f:
        f.write(json.dumps({'format':'ec-literature-v1','records':len(rows)})+'\n')
        for row in rows:f.write(json.dumps(row)+'\n')

def sample_rows():
    with gzip.open(PROJECT/'inputs/literature_evidence_20261019.jsonl.gz','rt',encoding='utf-8') as f:
        next(f);rows=[json.loads(next(f)) for _ in range(2)]
    for i,r in enumerate(rows):r['id']='test-literature-'+str(i);r['source']['id']='test-source-'+str(i)
    return rows

def test_additive_import_idempotence_and_atomic_cas_rejection(tmp_path):
    db=tmp_path/'catalog.sqlite';shutil.copyfile(DEFAULT_DB,db);pack=tmp_path/'pack.gz'
    rows=sample_rows();write_pack(pack,rows)
    with connect_readonly(db) as con:before=con.execute('SELECT count(*) FROM evidence').fetchone()[0];identity=[tuple(r) for r in con.execute('SELECT * FROM entities ORDER BY id')]
    assert import_pack(pack,db)['added']==2
    assert import_pack(pack,db)['added']==0
    with connect_readonly(db) as con:
        assert con.execute('SELECT count(*) FROM evidence').fetchone()[0]==before+2
        assert identity==[tuple(r) for r in con.execute('SELECT * FROM entities ORDER BY id')]
    bad=copy.deepcopy(rows);bad[0]['id']='rollback-test';bad[0]['source']['id']='rollback-source';bad[1]['source_cas']='80-05-8';write_pack(pack,bad)
    with pytest.raises(ValueError,match='CAS'):import_pack(pack,db)
    with connect_readonly(db) as con:assert not con.execute("SELECT 1 FROM evidence WHERE id='rollback-test'").fetchone()
    rows[0]['value']+=1;write_pack(pack,rows)
    with pytest.raises(ValueError,match='differs'):import_pack(pack,db)

def test_enriched_catalog_and_bounded_preview():
    from ec_agent.tools import EcologicalScreenTool
    with connect_readonly() as con:
        assert con.execute('SELECT count(*) FROM entities').fetchone()[0]==1216
        first=evidence_page(con,'EC0614',limit=12);second=evidence_page(con,'EC0614',page=2,limit=12)
        assert first['total']>100 and len(first['rows'])==12 and first['truncated']
        assert not {r['id'] for r in first['rows']} & {r['id'] for r in second['rows']}
        assert detail(con,'EC0614',evidence_limit=12)['evidence_count']==first['total']
        assert all(r['verification_level'] in {'database_curated','full_text_checked_with_limits'} and not r['identity_confirmed'] for r in first['rows'])
        six=evidence_page(con,'EC0872')['rows']
        assert len(six)==3 and all(r['verification_level']=='full_text_checked' for r in six)
        assert {r['value'] for r in six}=={.59,1.,1.96}
        assert con.execute('SELECT count(*) FROM exposures').fetchone()[0]==0
        assert not con.execute("SELECT 1 FROM evidence WHERE id LIKE 'ecotox-%' AND (evidence_type='PNEC' OR identity_confirmed<>0)").fetchone()
    value=json.loads(EcologicalScreenTool().run(entity_id='EC0614'))
    assert value['evidence_count']==first['total'] and len(value['evidence'])==12
    assert value['risk_score'] is None and not value['risk']['ratios']
