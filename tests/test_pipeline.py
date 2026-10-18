import json,sqlite3
from pathlib import Path
import pytest
from ec_agent.db import PROJECT,connect,initialize,stats
from ec_agent.ingest import import_catalog
from ec_agent.chemistry import analyze_smiles,analyze_all,cluster_all
from ec_agent.evidence import numeric_cell,import_epa,risk_results,to_ug_l

@pytest.fixture(scope='module')
def catalog(tmp_path_factory):
    db=tmp_path_factory.mktemp('catalog')/'test.sqlite'
    import_catalog(PROJECT/'inputs/catalog_snapshot.json',db)
    analyze_all(db)
    return db

def test_full_ingestion_and_independent_flags(catalog):
    with connect(catalog) as con:
        s=stats(con)
        assert s['entities']==1216
        assert s['experiment_candidates']==47 and s['measured']==172
        assert s['structure_records']==1108 and s['cas_present']==1116
        assert con.execute("SELECT count(*) FROM entities WHERE entity_type<>'单体/命名实体'").fetchone()[0]==82
        assert con.execute("SELECT count(*) FROM entities WHERE experiment_candidate=0").fetchone()[0]==1169
        assert not con.execute('PRAGMA foreign_key_check').fetchall()

def test_import_is_idempotent_and_preserves_review(catalog):
    with connect(catalog) as con:con.execute("UPDATE entities SET human_review='confirmed' WHERE id='EC0003'")
    import_catalog(PROJECT/'inputs/catalog_snapshot.json',catalog)
    with connect(catalog) as con:
        assert stats(con)['entities']==1216
        assert con.execute("SELECT human_review FROM entities WHERE id='EC0003'").fetchone()[0]=='confirmed'
        con.execute("UPDATE entities SET human_review='unreviewed' WHERE id='EC0003'")

def test_structural_group_specificity_and_salt_policy():
    assert '酰胺' in analyze_smiles('CC(=O)N')['functional_groups']
    assert '胺' not in analyze_smiles('CC(=O)N')['functional_groups']
    assert '胺' in analyze_smiles('CCN')['functional_groups']
    assert analyze_smiles('CC(=O)[O-].[Na+]')['eligible']
    assert not analyze_smiles('CCN.CCO')['eligible']
    assert not analyze_smiles('[Na+].[Cl-]')['eligible']
    assert not analyze_smiles('C[Hg]C')['eligible']
    large=analyze_smiles('C'*90)
    assert large['molecular_weight']>1000 and large['eligible']
    assert analyze_smiles('not a SMILES')['status']=='failed'

def test_deterministic_full_clustering_and_center_threshold(catalog):
    a=cluster_all(catalog,.4);b=cluster_all(catalog,.4)
    assert a['run_id']==b['run_id'] and a['parents']>900
    with connect(catalog) as con:
        rows=con.execute('SELECT * FROM cluster_members WHERE run_id=?',(a['run_id'],)).fetchall()
        assert len(rows)==a['parents']
        assert min(r['center_similarity'] for r in rows)>=.6-1e-9
        eligible=con.execute('''SELECT DISTINCT s.parent_key FROM entities e JOIN structures s ON s.id=e.selected_structure_id WHERE s.clustering_eligible=1''').fetchall()
        assert {r[0] for r in eligible}=={r['parent_key'] for r in rows}

def test_epa_qualifiers_and_idempotence(catalog):
    assert numeric_cell('> 40800')==('>',40800)
    assert numeric_cell('< 3D')==('<',3)
    assert numeric_cell('NR') is None
    assert numeric_cell('0') is None
    a=import_epa(PROJECT/'inputs/epa_benchmarks.json',catalog)
    with connect(catalog) as con:before=stats(con)['evidence_records']
    import_epa(PROJECT/'inputs/epa_benchmarks.json',catalog)
    with connect(catalog) as con:
        assert stats(con)['evidence_records']==before>100
        assert a['entities_matched']>30
        assert not con.execute("SELECT 1 FROM evidence WHERE evidence_type='PNEC'").fetchone()
        assert con.execute("SELECT count(*) FROM evidence WHERE relation<>'='").fetchone()[0]>0

def test_risk_requires_identity_exposure_and_matched_compartment_and_duration(catalog):
    with connect(catalog) as con:
        assert risk_results(con,'EC0499')['ratios']==[]
        eid=con.execute("SELECT entity_id FROM evidence WHERE compartment='freshwater' AND duration_class='acute' AND relation='=' LIMIT 1").fetchone()[0]
        con.execute("UPDATE entities SET human_review='confirmed' WHERE id=?",(eid,))
        assert risk_results(con,eid)['status']=='exposure_missing'
        con.execute("INSERT INTO exposures VALUES(?,?,?,?,?,?,?,?,?,?,?)",('test-x',eid,'sample','freshwater',1000,'ng/L','=','acute','2026-10-17','test-only',1))
        result=risk_results(con,eid)
        assert result['ratios']
        assert all(r['kind']=='benchmark_screening_quotient' for r in result['ratios'])
        assert all(r['compartment']=='freshwater' for r in result['ratios'])
        con.execute("UPDATE exposures SET qualifier='ND' WHERE id='test-x'")
        assert risk_results(con,eid)['ratios']==[]
        con.execute("UPDATE exposures SET qualifier='=',duration_class='unknown' WHERE id='test-x'")
        assert risk_results(con,eid)['ratios']==[]
        con.execute("DELETE FROM exposures WHERE id='test-x'")
        con.execute("UPDATE entities SET human_review='unreviewed' WHERE id=?",(eid,))
    assert to_ug_l(1000,'ng/L')==1
    with pytest.raises(ValueError):to_ug_l(2,'mg/kg')
