import json,os,re,socket,subprocess,sys,time,urllib.error,urllib.request
from pathlib import Path
import pytest
from ec_agent.db import PROJECT
from ec_agent import __version__

@pytest.fixture(scope='module')
def local_server(tmp_path_factory):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    output=tmp_path_factory.mktemp('server')/'server.log'
    env=os.environ.copy();env['PYTHONPATH']=str(PROJECT/'src');env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONUTF8']='1'
    # Never load a real model in integration tests.
    for key in ('EC_AGENT_API_KEY','EC_AGENT_MODEL','EC_AGENT_BASE_URL'):env.pop(key,None)
    log=output.open('w',encoding='utf-8')
    process=subprocess.Popen([sys.executable,'-B','-m','ec_agent.cli','serve','--port',str(port)],
        cwd=PROJECT,env=env,stdout=log,stderr=subprocess.STDOUT)
    url=f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            if process.poll() is not None:raise RuntimeError(output.read_text(encoding='utf-8'))
            try:
                with urllib.request.urlopen(url+'/api/health',timeout=1) as r:
                    if json.load(r)['version']==__version__:break
            except OSError:time.sleep(.2)
        else:raise RuntimeError('Local server failed to become ready')
        yield url
    finally:
        process.terminate();process.wait(timeout=15);log.close()

def post(url,payload):
    request=urllib.request.Request(url+'/api/analyze',data=json.dumps(payload).encode(),
        headers={'Content-Type':'application/json','Origin':url},method='POST')
    return urllib.request.urlopen(request,timeout=30)

def test_web_api_three_workflows_and_validation(local_server):
    for workflow in ('structure','neighbors','ecology'):
        with post(local_server,{'query':'EC0872','workflows':[workflow]}) as response:
            result=json.load(response)
        assert result['status']=='completed' and result['workflows']==[workflow]
    with pytest.raises(urllib.error.HTTPError) as error:
        post(local_server,{'query':'EC0872','workflows':['prediction']})
    assert error.value.code==400
    with urllib.request.urlopen(local_server) as response:html=response.read().decode('utf-8')
    assert 'id="analysis-workflow"' in html
    assert 'id="model-form"' in html
    assert '目录官能团为历史缓存' in html
    assert '一个助手，三条固定流程' not in html
    with urllib.request.urlopen(local_server+'/api/export?kind=clusters') as response:
        assert len(response.read())>1000

def test_web_javascript_syntax(tmp_path):
    import shutil
    node=shutil.which('node')
    if node is None:pytest.skip('Node is optional; not needed to run the application')
    html=(PROJECT/'src/ec_agent/web/index.html').read_text(encoding='utf-8')
    script=tmp_path/'web.js'
    script.write_text('\n'.join(re.findall(r'<script>(.*?)</script>',html,re.S)),encoding='utf-8')
    result=subprocess.run([node,'--check',str(script)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr

def test_runtime_json_resources_packaged():
    import tomllib
    config=tomllib.loads((PROJECT/'pyproject.toml').read_text(encoding='utf-8'))
    assert '*.json' in config['tool']['setuptools']['package-data']['ec_agent']
    for name in ('research_policy.json','functional_group_rules.json'):
        assert isinstance(json.loads((PROJECT/'src/ec_agent'/name).read_text(encoding='utf-8')),dict)
