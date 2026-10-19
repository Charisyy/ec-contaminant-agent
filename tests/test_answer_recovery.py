from types import SimpleNamespace
import pytest
from ec_agent.analysis_flow import run_entity_analysis
from ec_agent.crew import answer_question,EvidenceReview

@pytest.mark.parametrize('query,workflows,code',[
    ('双酚',['structure'],'unresolved_findings'),
    ('验收用不存在实体__EC_NO_MATCH__',['structure'],'unresolved_findings'),
    ('EC0001',['structure','neighbors','ecology'],'unobserved_source'),
    ('EC0010',['ecology'],'unobserved_source')])
def test_rejected_answer_preserves_real_tool_results(tmp_path,monkeypatch,query,workflows,code):
    run=run_entity_analysis(query,output_dir=tmp_path,workflows=workflows)
    bad=EvidenceReview(entity_ids=[run['entity_id']] if run['entity_id'] else [],answer='伪造结论不能展示',
        supported_findings=['伪造结论不能展示'],source_urls=['https://example.invalid/fabricated'] if run['entity_id'] else [],
        missing_inputs=[],risk_score=None)
    crew=SimpleNamespace(agents=[SimpleNamespace(tools=[SimpleNamespace(observations=[run])])],
        kickoff=lambda **kwargs:SimpleNamespace(pydantic=bad))
    monkeypatch.setattr('ec_agent.crew.make_crew',lambda *args,**kwargs:crew)
    reply=answer_question('离线模拟同类校验错误')
    assert reply['llm_used'] is True
    if run['entity_id']:
        assert reply['execution_mode']=='deterministic_fallback'
        assert reply['model_answer_accepted'] is False and reply['model_validation']['code']==code
    else:
        assert reply['execution_mode']=='directory_result'
        assert reply['model_answer_accepted'] is None and reply['model_validation'] is None
    assert '伪造结论' not in str(reply) and 'fabricated' not in str(reply)
    assert reply['workflow_runs']==[run] and reply['risk_score'] is None
    if not run['entity_id']:
        assert reply['entity_ids']==[] and reply['supported_findings']==[]
        assert '选择' in reply['answer'] if run['status']=='needs_entity_selection' else '未找到' in reply['answer']
    if query=='EC0010':
        assert 'EPA' in reply['answer'] and '对象：' in reply['answer'] and '时长类别：' in reply['answer']
        assert reply['source_urls'] and all('epa.gov' in url or 'pubchem' in url for url in reply['source_urls'])

def test_provider_failure_does_not_fabricate_local_reply(monkeypatch):
    def fail(**kwargs):raise RuntimeError('offline provider failure')
    monkeypatch.setattr('ec_agent.crew.make_crew',lambda *args,**kwargs:SimpleNamespace(kickoff=fail))
    with pytest.raises(RuntimeError,match='provider failure'):answer_question('离线测试服务失败')
