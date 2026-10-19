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
    for asset,kind in (('/chat.js','javascript'),('/chat.css','css'),('/filters.js','javascript'),('/filters.css','css')):
        with urllib.request.urlopen(local_server+asset) as response:
            assert kind in response.headers['Content-Type'] and len(response.read())>1000
    with urllib.request.urlopen(local_server+'/api/export?kind=clusters') as response:
        assert len(response.read())>1000

def test_web_javascript_syntax(tmp_path):
    import shutil
    node=shutil.which('node')
    if node is None:pytest.skip('Node is optional; not needed to run the application')
    html=(PROJECT/'src/ec_agent/web/index.html').read_text(encoding='utf-8')
    script=tmp_path/'web.js'
    script.write_text('\n'.join(re.findall(r'<script>(.*?)</script>',html,re.S)),encoding='utf-8')
    for source in (script,PROJECT/'src/ec_agent/web/chat.js',PROJECT/'src/ec_agent/web/filters.js'):
        result=subprocess.run([node,'--check',str(source)],capture_output=True,text=True)
        assert result.returncode==0,result.stderr

def test_runtime_json_resources_packaged():
    import tomllib
    config=tomllib.loads((PROJECT/'pyproject.toml').read_text(encoding='utf-8'))
    assert '*.json' in config['tool']['setuptools']['package-data']['ec_agent']
    assert 'web/*.js' in config['tool']['setuptools']['package-data']['ec_agent']
    assert 'web/*.css' in config['tool']['setuptools']['package-data']['ec_agent']
    for name in ('research_policy.json','functional_group_rules.json'):
        assert isinstance(json.loads((PROJECT/'src/ec_agent'/name).read_text(encoding='utf-8')),dict)

def test_literature_pagination_and_complete_csv(local_server):
    import csv,io
    with urllib.request.urlopen(local_server+'/api/evidence/EC0614?limit=10') as r:first=json.load(r)
    with urllib.request.urlopen(local_server+'/api/evidence/EC0614?limit=10&page=2') as r:second=json.load(r)
    assert first['total']>100 and len(first['rows'])==len(second['rows'])==10
    assert {r['id'] for r in first['rows']}.isdisjoint(r['id'] for r in second['rows'])
    with urllib.request.urlopen(local_server+'/api/evidence/EC0614?format=csv') as r:
        assert 'attachment' in r.headers['Content-Disposition']
        rows=list(csv.DictReader(io.StringIO(r.read().decode('utf-8-sig'))))
    assert len(rows)==first['total'] and all(r['source_metadata'] for r in rows)
    with pytest.raises(urllib.error.HTTPError) as error:urllib.request.urlopen(local_server+'/api/evidence/DOESNOTEXIST')
    assert error.value.code==404
