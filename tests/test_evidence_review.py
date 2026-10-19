import copy,json,shutil
import pytest
from ec_agent.db import DEFAULT_DB,PROJECT,connect_readonly
from ec_agent.literature import evidence_page,import_pack
from ec_agent.evidence_review import apply_reviews

PACK=PROJECT/'inputs/literature_review_20261019.json'

def test_raw_records_preserved_and_derived_values_quarantined():
    with connect_readonly() as c:
        assert c.execute('SELECT count(*) FROM evidence').fetchone()[0]==22448
        assert c.execute('SELECT count(*) FROM usable_evidence').fetchone()[0]==22443
        assert c.execute("SELECT count(*) FROM reviewed_evidence WHERE review_decision='excluded'").fetchone()[0]==3
        assert c.execute("SELECT count(*) FROM reviewed_evidence WHERE review_decision='needs_review'").fetchone()[0]==2
        for eid in ['ecotox-2337284-EC0270','ecotox-2337297-EC0322','ecotox-2337291-EC0347']:
            assert c.execute('SELECT evidence_type FROM evidence WHERE id=?',(eid,)).fetchone()[0]=='NOEC'
            assert not c.execute('SELECT 1 FROM usable_evidence WHERE id=?',(eid,)).fetchone()
        page=evidence_page(c,'EC0270')
        assert page['summary']['withheld_count']==1 and len(page['withheld_rows'])==1
        assert '1000' in page['withheld_rows'][0]['review']['conclusion']
        assert not c.execute('SELECT 1 FROM exposures').fetchone()

def test_primary_review_bounds_endpoint_basis_and_qualified_medium():
    with connect_readonly() as c:
        records=evidence_page(c,'EC0322')['rows']
        bound=next(r for r in records if r['id']=='ecotox-2047302-EC0322')
        assert bound['verification_level']=='full_text_checked' and bound['relation']=='>' and bound['value']==100
        assert '不能活动' in bound['endpoint'] and '非实测' in bound['review_note']
        adult=next(r for r in records if r['id']=='ecotox-2337296-EC0322')
        assert adult['value']==61.5 and adult['compartment']=='water_unspecified' and adult['original_compartment']=='marine'
        assert adult['verification_level']=='full_text_checked_with_limits'
        assert not adult['identity_confirmed']
        raw=c.execute('SELECT compartment,notes FROM evidence WHERE id=?',(adult['id'],)).fetchone()
        assert raw['compartment']=='marine' and json.loads(raw['notes'])['verification_level']=='database_curated'
        bpa=evidence_page(c,'EC0614')['rows']
        lee=next(r for r in bpa if r['id']=='ecotox-2294441-EC0614')
        assert '局部组织' in lee['review_note'] and '整体GFP' in lee['endpoint']

def test_abstract_verification_does_not_promote_unchecked_fields():
    with connect_readonly() as c:
        records=evidence_page(c,'EC0218',limit=200)['rows']
        r=next(r for r in records if r['id']=='ecotox-2168570-EC0218')
        assert r['verification_level']=='abstract_checked'
        assert '时长' in r['review']['unresolved_fields']
        noec=next(r for r in records if r['id']=='ecotox-2168567-EC0218')
        assert noec['verification_level']=='database_curated'

def test_append_only_review_idempotence_conflict_and_atomic_rollback(tmp_path):
    db=tmp_path/'copy.sqlite';shutil.copyfile(DEFAULT_DB,db)
    assert apply_reviews(PACK,db)['added']==0
    # Original evidence pack still imports without a conflict after review.
    assert import_pack(PROJECT/'inputs/literature_evidence_20261019.jsonl.gz',db)['added']==0
    original=json.loads(PACK.read_text(encoding='utf-8'));bad=copy.deepcopy(original)
    bad['reviews'][0]['conclusion']='unauthorized overwrite';path=tmp_path/'bad.json';path.write_text(json.dumps(bad),encoding='utf-8')
    with pytest.raises(ValueError,match='history'):apply_reviews(path,db)
    bad=copy.deepcopy(original);bad['reviews']=bad['reviews'][:2]
    bad['reviews'][0]['id']='rollback-event';bad['reviews'][1]['evidence_sha256']='stale-digest'
    path.write_text(json.dumps(bad),encoding='utf-8')
    with pytest.raises(ValueError,match='changed'):apply_reviews(path,db)
    with connect_readonly(db) as c:assert not c.execute("SELECT 1 FROM evidence_reviews WHERE id='rollback-event'").fetchone()

def test_tool_preview_and_directory_counts_use_current_evidence():
    from ec_agent.tools import EcologicalScreenTool
    from ec_agent.db import search,stats
    value=json.loads(EcologicalScreenTool().run(entity_id='EC0270'))
    assert value['evidence_count']==100 and value['evidence_summary']['withheld_count']==1
    assert 'ecotox-2337284-EC0270' not in {r['id'] for r in value['evidence']}
    with connect_readonly() as c:
        assert search(c,q='EC0270')['rows'][0]['evidence_count']==100
        s=stats(c);assert s['evidence_records']==22443 and s['evidence_records_archived']==22448
