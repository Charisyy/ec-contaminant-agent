from types import SimpleNamespace
import json
import pytest
from ec_agent.citations import citation_sources
from ec_agent.crew import ModelExplanation,checked_result,answer_question
from ec_agent.conversation import AnswerValidationError
from ec_agent.tools import RunResearchWorkflowTool

def observed(entity,url):
    return {'entity_id':entity,'status':'completed','provenance':{'sources':[{'url':url}]}}

def explain(entity,refs,answer='工具已返回结果。'):
    return SimpleNamespace(pydantic=ModelExplanation(entity_ids=[entity],answer=answer,supported_findings=[],
        source_refs=refs,missing_inputs=[],risk_score=None))

def test_citation_ref_is_stable_and_urls_are_assembled_from_actual_sources():
    first=observed('EC0010','https://example.org/evidence?a=1&b=2')
    second=observed('EC0872','https://example.org/structure')
    ref=citation_sources([first])[0]['ref']
    assert ref==next(s['ref'] for s in citation_sources([second,first]) if s['url'].endswith('b=2'))
    reply=checked_result(explain('EC0010',[ref,ref]),[first,second])
    assert reply['source_urls']==['https://example.org/evidence?a=1&b=2']
    assert 'source_refs' not in reply and reply['model_answer_accepted'] is True

@pytest.mark.parametrize('bad_ref',['SRC_invented','https://example.org/evidence'])
def test_unknown_refs_or_raw_urls_are_rejected(bad_ref):
    with pytest.raises(AnswerValidationError,match='来源编号'):
        checked_result(explain('EC0010',[bad_ref]),[observed('EC0010','https://example.org/evidence')])

def test_citation_must_belong_to_the_selected_entity():
    first=observed('EC0010','https://example.org/a');second=observed('EC0872','https://example.org/b')
    ref=citation_sources([second])[0]['ref']
    with pytest.raises(AnswerValidationError):checked_result(explain('EC0010',[ref]),[first,second])

def test_unknown_url_in_answer_text_is_not_a_citation_bypass():
    with pytest.raises(AnswerValidationError,match='正文'):
        checked_result(explain('EC0010',[],answer='引用 https://unobserved.example/claim。'),[observed('EC0010','https://example.org/a')])

def test_tool_exposes_only_observed_citation_identifiers(tmp_path):
    tool=RunResearchWorkflowTool(output_dir=str(tmp_path))
    value=json.loads(tool.run(query='EC0010',workflows=['ecology']))
    sources=value['citation_sources']
    assert sources and all(source['ref'].startswith('SRC_') for source in sources)
    assert {source['url'] for source in sources}=={source['url'] for source in tool.observations[0]['provenance']['sources'] if source.get('url')}

@pytest.mark.parametrize('status',['needs_entity_selection','not_found'])
def test_directory_reply_survives_unusable_model_explanation(status,monkeypatch):
    run={'status':status,'entity_id':None,'selection':{'total':2 if status=='needs_entity_selection' else 0,
        'choices':[{'entity_id':'EC0010','name':'候选','cas':'76-06-2'}] if status=='needs_entity_selection' else []}}
    def fail(**kwargs):raise RuntimeError('offline explanation interrupted after actual lookup')
    crew=SimpleNamespace(agents=[SimpleNamespace(tools=[SimpleNamespace(observations=[run])])],kickoff=fail)
    monkeypatch.setattr('ec_agent.crew.make_crew',lambda *args,**kwargs:crew)
    reply=answer_question('本地目录状态测试')
    assert reply['execution_mode']=='directory_result' and reply['entity_ids']==[]
    assert reply['supported_findings']==[] and reply['source_urls']==[] and reply['risk_score'] is None
