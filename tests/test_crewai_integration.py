import json
from pathlib import Path
from crewai import BaseLLM
from ec_agent.db import PROJECT,connect,stats
from ec_agent.workflow import CatalogFlow
from ec_agent.crew import make_crew
from ec_agent.tools import CatalogSearchTool,StructureEvidenceTool

def test_real_crewai_flow_without_network_or_model(tmp_path):
    flow=CatalogFlow()
    flow.state.db_path=str(tmp_path/'full.sqlite')
    flow.state.output_dir=str(tmp_path/'outputs')
    result=flow.kickoff()
    assert result['framework']=='CrewAI Flow'
    assert result['scope']=='ALL_CATALOG_ENTITIES' and result['llm_used'] is False
    assert result['statistics']['entities']==1216
    assert len(result['stages']['clustering'])==3
    assert (tmp_path/'outputs/entities.csv').exists()
    assert result['statistics']['exposure_records']==0
    assert all(v['distance_threshold'] in (.3,.4,.5) for v in result['stages']['clustering'])

class OfflineContractLLM(BaseLLM):
    """Contract test only. Does not represent a real external model evaluation."""
    def __init__(self):
        super().__init__(model='offline-contract-fixture',temperature=0)
        self.calls=0
    def call(self,messages,tools=None,callbacks=None,available_functions=None,**kwargs):
        self.calls+=1
        payload={'entity_ids':['EC0872'],'answer':'离线接口契约测试：不是化学或毒性结论。','supported_findings':[],'source_urls':[],'missing_inputs':['需要真实模型验证自然语言效果'],'risk_score':None}
        return 'Final Answer: '+json.dumps(payload,ensure_ascii=False)
    def supports_function_calling(self):return False
    def supports_stop_words(self):return False
    def get_context_window_size(self):return 32000

def test_agent_tools_and_single_agent_wiring():
    llm=OfflineContractLLM();crew=make_crew(llm=llm)
    assert len(crew.agents)==1 and len(crew.tasks)==1
    assert [tool.name for tool in crew.agents[0].tools]==['run_research_workflow']
    assert crew.agents[0].allow_delegation is False
    rows=json.loads(CatalogSearchTool().run(query='EC0872'))
    assert rows['total']==1
    record=json.loads(StructureEvidenceTool().run(entity_id='EC0872'))
    assert record['id']=='EC0872'
    assert record['risk']['ratios']==[]
    result=crew.kickoff(inputs={'question':'仅测试接口契约，不产生科学结论。'})
    assert result.pydantic is not None and result.pydantic.risk_score is None
    assert llm.calls>=1

def test_gpt6_actual_provider_request_contract_without_network(monkeypatch):
    monkeypatch.setenv('EC_AGENT_MODEL','openai/gpt-6.1-sol')
    monkeypatch.setenv('EC_AGENT_API_KEY','offline-test-placeholder')
    monkeypatch.setenv('EC_AGENT_BASE_URL','https://api.openai.com/v1')
    crew=make_crew()
    llm=crew.agents[0].llm
    assert llm.api=='responses'
    params=llm._prepare_responses_params([{'role':'user','content':'offline contract test'}],tools=[{
        'type':'function','function':{'name':'catalog_search','description':'read local catalog','parameters':{'type':'object','properties':{'query':{'type':'string'}},'required':['query']}}
    }])
    assert params['model']=='gpt-6.1-sol'
    assert params['reasoning']=={'effort':'medium'}
    assert params['store'] is False
    assert 'temperature' not in params and 'top_p' not in params
    assert params['tools'][0]['name']=='catalog_search'
    assert 'reasoning.encrypted_content' in params['include']
