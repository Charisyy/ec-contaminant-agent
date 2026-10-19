import hashlib,json,shutil,sqlite3
import pytest
from pydantic import ValidationError
from ec_agent.db import DEFAULT_DB,connect,connect_readonly
from ec_agent.policy import WorkflowRequest
from ec_agent.tools import (EntityInput,SimilarInput,SearchInput,ParseStructureTool,
    FunctionalGroupTool,EcologicalScreenTool,ClusterMembershipTool,RunResearchWorkflowTool)
from ec_agent.analysis_flow import run_entity_analysis
from ec_agent.crew import answer_question,make_crew
from test_crewai_integration import OfflineContractLLM

def test_tool_arguments_reject_unknown_paths_and_scientific_overrides():
    for cls,kwargs in [(EntityInput,{'entity_id':'EC0872','db_path':'other.sqlite'}),
        (EntityInput,{'entity_id':'invalid'}),(SimilarInput,{'entity_id':'EC0872','limit':2000}),
        (SearchInput,{'query':''}),(WorkflowRequest,{'query':'   '}),
        (WorkflowRequest,{'query':'EC0872','workflows':[]}),
        (WorkflowRequest,{'query':'EC0872','workflows':['prediction']}),
        (WorkflowRequest,{'query':'EC0872','workflows':['ecology','ecology']}),
        (WorkflowRequest,{'query':'EC0872','radius':3})]:
        with pytest.raises(ValidationError):cls(**kwargs)

@pytest.mark.parametrize('workflow,tools',[
    ('structure',['catalog_search','entity_structure_and_evidence','parse_catalog_structure','detect_functional_groups']),
    ('neighbors',['catalog_search','entity_structure_and_evidence','parse_catalog_structure','find_similar_entities','inspect_structure_clusters']),
    ('ecology',['catalog_search','entity_structure_and_evidence','screen_ecological_evidence'])])
def test_independent_workflows_execute_only_needed_tools(tmp_path,workflow,tools):
    result=run_entity_analysis('EC0872',output_dir=tmp_path,workflows=[workflow])
    assert result['status']=='completed' and result['entity_id']=='EC0872'
    assert result['llm_used'] is False
    assert [t['tool'] for t in result['trace']]==tools
    assert result['workflows']==[workflow]
    assert (tmp_path/result['artifact_name']).exists()

def test_combined_flow_runs_shared_steps_once_and_preserves_database(tmp_path):
    before=hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest()
    result=run_entity_analysis('EC0872',output_dir=tmp_path)
    assert result['status']=='completed'
    names=[t['tool'] for t in result['trace']]
    assert len(names)==7 and names.count('parse_catalog_structure')==1
    assert result['results']['structure']['identifier_consistent']
    assert result['results']['functional_groups']['rules_version']=='1.2'
    assert result['results']['functional_groups']['functional_groups']['醌片段']['count']==1
    assert result['results']['ecology']['risk']['ratios']==[]
    assert result['methods']['similarity']['radius']==2 and result['risk_score'] is None
    assert hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest()==before

def test_ambiguous_search_stops_before_identity_and_computation(tmp_path):
    result=run_entity_analysis('双酚',output_dir=tmp_path)
    assert result['status']=='needs_entity_selection' and result['selection']['total']>1
    assert result['entity_id'] is None and len(result['trace'])==1 and result['results']=={}

def test_agent_cannot_resolve_ambiguity_itself(tmp_path):
    tool=RunResearchWorkflowTool(output_dir=str(tmp_path))
    first=json.loads(tool.run(query='双酚',workflows=['structure']))
    second=json.loads(tool.run(query=first['selection']['choices'][0]['entity_id'],workflows=['structure']))
    assert second['status']=='needs_entity_selection'
    assert len(tool.observations)==1

def test_missing_structure_still_checks_evidence(tmp_path):
    with connect_readonly() as con:eid=con.execute('SELECT id FROM entities WHERE selected_structure_id IS NULL ORDER BY id LIMIT 1').fetchone()[0]
    result=run_entity_analysis(eid,output_dir=tmp_path)
    assert result['status']=='completed'
    assert result['results']['structure']['status']=='not_applicable'
    assert result['results']['functional_groups']['functional_groups']=={}
    assert result['results']['similarity']['status']=='not_applicable'
    assert result['results']['ecology']['risk_score'] is None
    assert all(not s['executed'] for s in result['skipped_steps'])
    assert [t['tool'] for t in result['trace']]==['catalog_search','entity_structure_and_evidence','screen_ecological_evidence']

def test_material_and_identity_conflict_do_not_block_evidence(tmp_path):
    db=tmp_path/'copy.sqlite';shutil.copyfile(DEFAULT_DB,db)
    for field,value in [('entity_type','材料/混合物'),('human_review','needs_correction')]:
        with connect(db) as con:
            con.execute("UPDATE entities SET entity_type='单体/命名实体',human_review='unreviewed' WHERE id='EC0872'")
            con.execute(f'UPDATE entities SET {field}=? WHERE id=?',(value,'EC0872'))
        result=run_entity_analysis('EC0872',db,output_dir=tmp_path/'runs')
        assert result['workflow_statuses']['structure']=='not_applicable'
        assert result['workflow_statuses']['ecology']=='completed'

def test_failed_structure_tool_preserves_ecology_and_artifact(tmp_path,monkeypatch):
    def fail(*args,**kwargs):raise RuntimeError('offline fixture')
    monkeypatch.setattr(ParseStructureTool,'_run',fail)
    result=run_entity_analysis('EC0872',output_dir=tmp_path)
    assert result['status']=='partial'
    assert result['results']['structure']['status']=='failed'
    assert result['workflow_statuses']['ecology']=='completed'
    assert json.loads((tmp_path/result['artifact_name']).read_text(encoding='utf-8'))['status']=='partial'

def test_failed_model_review_keeps_saved_tool_results(tmp_path,monkeypatch):
    def unavailable(*args,**kwargs):raise RuntimeError('offline model unavailable')
    monkeypatch.setattr('ec_agent.crew.review_analysis',unavailable)
    result=run_entity_analysis('EC0872',llm_review=True,output_dir=tmp_path)
    assert result['llm_used'] is False and result['results']['model_review']['status']=='failed'
    assert len(result['trace'])==7
    assert json.loads((tmp_path/result['artifact_name']).read_text(encoding='utf-8'))['results']['structure']['identifier_consistent']

def test_readonly_connection_refuses_writes_and_missing_database(tmp_path):
    with connect_readonly() as con:
        with pytest.raises(sqlite3.OperationalError):con.execute("UPDATE entities SET name=name WHERE id='EC0872'")
    missing=tmp_path/'missing.sqlite'
    with pytest.raises(FileNotFoundError):
        with connect_readonly(missing):pass
    assert not missing.exists()

def test_historical_rule_cache_and_changed_cluster_config_are_visible(tmp_path):
    db=tmp_path/'copy.sqlite';shutil.copyfile(DEFAULT_DB,db)
    with connect(db) as con:
        con.execute("UPDATE structures SET features_json=json_set(features_json,'$.rules_version','0.0') WHERE id=(SELECT selected_structure_id FROM entities WHERE id='EC0872')")
        con.execute("UPDATE cluster_runs SET config_json=json_set(config_json,'$.radius',3)")
    result=run_entity_analysis('EC0872',db,output_dir=tmp_path/'runs')
    assert result['results']['structure']['cache_status']=='historical'
    assert result['results']['functional_groups']['rules_version']=='1.2'
    assert result['results']['cluster']['status']=='historical'
    assert 'radius' in result['results']['cluster']['mismatched_fields']
    assert result['status']=='partial'
    with connect_readonly(db) as con:
        assert con.execute("SELECT json_extract(features_json,'$.rules_version') FROM structures WHERE id=(SELECT selected_structure_id FROM entities WHERE id='EC0872')").fetchone()[0]=='0.0'

class ScriptedWorkflowLLM(OfflineContractLLM):
    def call(self,messages,**kwargs):
        if self.calls==0:
            self.calls+=1
            return 'Thought: run the approved workflow.\nAction: run_research_workflow\nAction Input: '+json.dumps({'query':'EC0872','workflows':['structure','ecology']})
        return super().call(messages,**kwargs)

def test_single_agent_really_calls_fixed_workflow_without_network(tmp_path):
    llm=ScriptedWorkflowLLM()
    result=answer_question('检查EC0872的结构与生态证据',llm=llm,output_dir=tmp_path)
    assert len(result['workflow_runs'])==1
    assert result['workflow_runs'][0]['workflows']==['structure','ecology']
    assert 'find_similar_entities' not in [r['tool'] for r in result['workflow_runs'][0]['trace']]
    assert llm.calls>=2

def test_model_cannot_answer_without_executing_workflow(tmp_path):
    with pytest.raises(ValueError,match='没有执行固定工作流'):
        answer_question('检查EC0872',llm=OfflineContractLLM(),output_dir=tmp_path)

def test_existing_rule_11_does_not_count_nitro_as_amine():
    from ec_agent.chemistry import analyze_smiles
    groups=analyze_smiles('C[N+](=O)[O-]')['functional_groups']
    assert '硝基' in groups and '胺' not in groups

@pytest.mark.parametrize('field,value',[('entity_ids',['EC0001']),('source_urls',['https://example.invalid/fabricated']),('risk_score',1.0)])
def test_answer_guard_rejects_unobserved_ids_urls_and_risk_scores(field,value):
    from types import SimpleNamespace
    from ec_agent.crew import EvidenceReview,checked_result
    payload={'entity_ids':['EC0872'],'answer':'离线校验','supported_findings':[],
        'source_urls':[],'missing_inputs':[],'risk_score':None}
    payload[field]=value
    with pytest.raises(ValueError):
        checked_result(SimpleNamespace(pydantic=EvidenceReview(**payload)),
            [{'run_id':'fixture','entity_id':'EC0872','status':'completed','provenance':{'sources':[]}}])

def test_review_reuses_results_without_executing_another_workflow(tmp_path,monkeypatch):
    from ec_agent.crew import review_analysis
    analysis=run_entity_analysis('EC0872',output_dir=tmp_path,workflows=['ecology'])
    def forbidden(*args,**kwargs):raise AssertionError('Review must not run another tool workflow')
    monkeypatch.setattr(RunResearchWorkflowTool,'_run',forbidden)
    review=review_analysis(analysis,llm=OfflineContractLLM())
    assert review['workflow_run_ids']==[analysis['run_id']]
    assert 'workflow_runs' not in review
