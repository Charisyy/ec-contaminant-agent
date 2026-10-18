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

def test_configured_adapter_executes_real_tools_against_mock_model(isolated_model,monkeypatch,tmp_path):
    monkeypatch.setenv('NO_PROXY','127.0.0.1,localhost')
    monkeypatch.setenv('no_proxy','127.0.0.1,localhost')
    calls=[]
    class MockModel(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append(data)
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
        from ec_agent.crew import answer_question
        result=answer_question('分析 EC0011 的官能团',output_dir=tmp_path/'runs')
        assert result['entity_ids']==['EC0011'] and result['risk_score'] is None
        assert result['workflow_runs'][0]['trace'] and len(calls)>=2
    finally:instance.shutdown();instance.server_close();thread.join(timeout=5)
