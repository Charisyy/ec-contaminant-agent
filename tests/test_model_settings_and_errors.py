"""Failure recovery and local credential handling; no external model requests."""
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import threading
import urllib.error
import urllib.request
import pytest
from ec_agent import model_settings as settings
from ec_agent import server
from ec_agent.db import DEFAULT_DB

@pytest.fixture
def isolated_model(monkeypatch,tmp_path):
    for key in settings.KEYS:monkeypatch.setenv(key,'')
    path=tmp_path/'.env'
    monkeypatch.setattr(settings,'ENV_PATH',path)
    return path

def test_save_preserve_restart_clear_without_revealing_key(isolated_model):
    from dotenv import dotenv_values
    isolated_model.write_text('EC_AGENT_PORT=8899\n',encoding='utf-8')
    key="offline-fixture-'credential"
    result=settings.save_settings({'provider':'deepseek','api_key':key})
    assert result['configured'] and result['key_configured']
    assert result['model']=='deepseek-flash'
    assert key not in json.dumps(result) and 'api_key' not in result
    saved=dotenv_values(isolated_model)
    assert saved['EC_AGENT_API_KEY']==key and saved['EC_AGENT_PORT']=='8899'
    settings.save_settings({'provider':'deepseek','api_key':''})
    assert dotenv_values(isolated_model)['EC_AGENT_API_KEY']==key
    result=settings.save_settings({'clear':True})
    assert not result['configured'] and not result['key_configured']
    assert dotenv_values(isolated_model)['EC_AGENT_PORT']=='8899'

@pytest.mark.parametrize('payload',[
    {'provider':'compatible','base_url':'https://example.org/v1?secret=x','model':'x','api_key':'offline'},
    {'provider':'compatible','base_url':'http://example.org/v1','model':'x','api_key':'offline'},
    {'provider':'compatible','base_url':'https://example.org/v1/chat/completions','model':'x','api_key':'offline'},
    {'provider':'deepseek','api_key':'a\nb'},
    {'provider':'invented','api_key':'offline'},
    {'provider':'deepseek','api_key':'offline','sql':'select 1'},
])
def test_invalid_config_does_not_change_file(isolated_model,payload):
    isolated_model.write_text('EC_AGENT_PORT=8899\n',encoding='utf-8')
    original=isolated_model.read_bytes()
    with pytest.raises(ValueError):settings.save_settings(payload)
    assert isolated_model.read_bytes()==original

@pytest.fixture
def http_app(isolated_model):
    instance=server.create_server(DEFAULT_DB,0)
    thread=threading.Thread(target=instance.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{instance.server_port}'
    finally:instance.shutdown();instance.server_close();thread.join(timeout=5)

def request(url,path,data=None,origin=None):
    req=urllib.request.Request(url+path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={'Content-Type':'application/json','Origin':origin or url})
    try:
        with urllib.request.urlopen(req,timeout=30) as response:return response.status,json.load(response)
    except urllib.error.HTTPError as error:return error.code,json.load(error)

def test_settings_api_has_origin_checks_and_no_key_echo(http_app,isolated_model):
    code,result=request(http_app,'/api/model-settings',{'provider':'deepseek','api_key':'offline-fixture-key'})
    assert code==200 and result['configured'] and 'offline-fixture-key' not in json.dumps(result)
    code,result=request(http_app,'/api/model-settings')
    assert code==200 and 'offline-fixture-key' not in json.dumps(result)
    code,_=request(http_app,'/api/model-settings',{'clear':True},origin='http://untrusted.example')
    assert code==403 and settings.public_settings()['configured']
    code,_=request(http_app,'/api/model-settings',{'clear':True})
    assert code==200
    code,result=request(http_app,'/api/ask',{'question':'分析 EC0011'})
    assert code==409 and result['code']=='model_not_configured'

def test_workflow_exceptions_return_json_and_server_survives(http_app,monkeypatch):
    from ec_agent import analysis_flow
    def missing(*args,**kwargs):raise ModuleNotFoundError("No module named 'crewai'",name='crewai')
    monkeypatch.setattr(analysis_flow,'run_entity_analysis',missing)
    code,result=request(http_app,'/api/analyze',{'query':'可卡因'})
    assert code==503 and result['code']=='missing_dependency' and 'crewai' in result['error']
    def failed(*args,**kwargs):raise OSError('not safe to expose: offline-secret-fixture')
    monkeypatch.setattr(analysis_flow,'run_entity_analysis',failed)
    code,result=request(http_app,'/api/analyze',{'query':'可卡因'})
    assert code==500 and result['request_id'] and 'offline-secret-fixture' not in json.dumps(result)
    code,result=request(http_app,'/api/health')
    assert code==200 and result['environment']['ready']

@pytest.mark.parametrize('profile',['compatible','deepseek'])
def test_configured_adapter_executes_real_tools_against_mock_model(isolated_model,monkeypatch,tmp_path,profile):
    monkeypatch.setenv('NO_PROXY','127.0.0.1,localhost')
    monkeypatch.setenv('no_proxy','127.0.0.1,localhost')
    calls=[]
    class MockModel(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(data)
            if profile=='deepseek' and (data.get('thinking')!={'type':'disabled'} or data.get('response_format',{}).get('type')=='json_schema'):
                body=b'{"error":{"message":"fixture: thinking protocol not selected","type":"invalid_request_error"}}'
                self.send_response(400);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body);return
            invoked=any(message.get('role')=='tool' for message in data['messages'])
            if not invoked and data.get('tools'):
                message={'role':'assistant','content':None,'tool_calls':[{'id':'call_fixture','type':'function',
                    'function':{'name':'run_research_workflow','arguments':json.dumps({'query':'EC0011','workflows':['structure']})}}]}
                reason='tool_calls'
            else:
                message={'role':'assistant','content':json.dumps({'entity_ids':['EC0011'],
                    'answer':'本地接口契约测试。','supported_findings':[],'source_urls':[],
                    'missing_inputs':['真实服务仍需验证'],'risk_score':None},ensure_ascii=False)}
                reason='stop'
            body=json.dumps({'id':'chatcmpl-fixture','object':'chat.completion','created':1,
                'model':'local-fixture','choices':[{'index':0,'message':message,'finish_reason':reason}],
                'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    instance=ThreadingHTTPServer(('127.0.0.1',0),MockModel)
    thread=threading.Thread(target=instance.serve_forever,daemon=True);thread.start()
    try:
        settings.save_settings({'provider':'compatible','base_url':f'http://127.0.0.1:{instance.server_port}/v1',
            'model':'local-fixture','api_key':'offline-fixture-key'})
        if profile=='deepseek':monkeypatch.setenv('EC_AGENT_PROVIDER','deepseek')
        from ec_agent.crew import answer_question
        result=answer_question('分析 EC0011 的官能团',output_dir=tmp_path/'runs')
        assert result['entity_ids']==['EC0011'] and result['risk_score'] is None
        assert result['workflow_runs'][0]['trace'] and len(calls)>=2
        if profile=='deepseek':assert all(call['thinking']=={'type':'disabled'} for call in calls)
    finally:instance.shutdown();instance.server_close();thread.join(timeout=5)

@pytest.mark.parametrize('question',['你好','您好！','Hello','谢谢你'])
def test_help_does_not_create_agent_or_request_model(monkeypatch,question):
    from ec_agent import crew
    def forbidden(*args,**kwargs):raise AssertionError('Help must not create an agent')
    monkeypatch.setattr(crew,'make_crew',forbidden)
    result=crew.answer_question(question)
    assert result['execution_mode']=='local_help' and result['llm_used'] is False
    assert result['workflow_runs']==[] and result['risk_score'] is None

@pytest.mark.parametrize('question',['你好，请分析 EC0011','分析可卡因','EC0872','你好可卡因'])
def test_help_does_not_swallow_research_questions(question):
    from ec_agent.conversation import local_help
    assert local_help(question) is None

def test_help_and_model_errors_are_separate_http_results(http_app,isolated_model,monkeypatch):
    from ec_agent import crew
    from ec_agent.conversation import AnswerValidationError
    code,result=request(http_app,'/api/ask',{'question':'你好'})
    assert code==200 and result['execution_mode']=='local_help' and not result['llm_used']
    settings.save_settings({'provider':'deepseek','api_key':'offline-fixture-key'})
    def invalid(*args,**kwargs):raise AnswerValidationError('workflow_not_called','模型没有执行固定工作流。')
    monkeypatch.setattr(crew,'answer_question',invalid)
    code,result=request(http_app,'/api/ask',{'question':'分析 EC0011'})
    assert code==422 and result['stage']=='answer_validation' and result['code']=='workflow_not_called'
    class ProtocolError(Exception):status_code=400
    def bad_request(*args,**kwargs):raise ProtocolError('private fixture message must not be exposed')
    monkeypatch.setattr(crew,'answer_question',bad_request)
    code,result=request(http_app,'/api/ask',{'question':'分析 EC0011'})
    assert code==502 and result['stage']=='model_request' and result['provider_status']==400
    assert result['request_id'] and 'private fixture' not in json.dumps(result)

@pytest.mark.parametrize('status,code',[(400,'model_protocol'),(401,'model_authentication'),
    (402,'model_balance'),(429,'model_rate_limit'),(404,'model_not_found'),(503,'model_service')])
def test_nested_provider_errors_are_classified_without_messages(status,code):
    class ProviderFailure(Exception):pass
    inner=ProviderFailure('private credential fixture');inner.status_code=status
    outer=RuntimeError('wrapper');outer.__cause__=inner
    result=settings.model_failure_details(outer)
    assert result['code']==code and result['provider_status']==status
    assert 'private credential' not in json.dumps(result)

@pytest.mark.parametrize('entity_id',['EC9999','EC0011; DROP TABLE entities',{'id':'EC0011'},'ec0011'])
def test_invalid_context_is_rejected_before_agent_creation(entity_id,monkeypatch):
    from ec_agent import crew
    from ec_agent.conversation import ConversationContextError
    def forbidden(*args,**kwargs):raise AssertionError('Invalid context must not create an agent')
    monkeypatch.setattr(crew,'make_crew',forbidden)
    with pytest.raises(ConversationContextError):crew.answer_question('它有哪些官能团？',context_entity_id=entity_id)

def test_followup_context_uses_catalog_identity_without_claiming_confirmation():
    from ec_agent.conversation import question_with_context
    value=question_with_context('它有哪些官能团？','EC0011',DEFAULT_DB)
    assert value.startswith('它有哪些官能团？') and '可卡因' in value and '50-36-2' in value
    assert '不表示化学身份已确认' in value and '以当前问题为准' in value

def test_context_error_returns_actionable_http_400(http_app,isolated_model,monkeypatch):
    settings.save_settings({'provider':'deepseek','api_key':'offline-fixture-key'})
    from ec_agent import crew
    def forbidden(*args,**kwargs):raise AssertionError('Invalid context must not request a model')
    monkeypatch.setattr(crew,'make_crew',forbidden)
    code,result=request(http_app,'/api/ask',{'question':'它有哪些官能团？','context_entity_id':'EC9999'})
    assert code==400 and result['code']=='invalid_context'
