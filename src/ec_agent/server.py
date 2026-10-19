from __future__ import annotations
import csv,io,json,os,sqlite3,sys,logging,uuid,importlib.util
from logging.handlers import RotatingFileHandler
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from .db import DEFAULT_DB,PROJECT,connect,stats,search,detail,dumps,now
from .chemistry import nearest
from .evidence import first_pass,risk_results
from . import __version__
from .model_settings import public_settings,save_settings,test_connection,model_failure_details
from .conversation import local_help,ConversationContextError
from .literature import evidence_page

def environment_status():
    missing=[name for name in ('crewai','rdkit','pydantic','dotenv') if importlib.util.find_spec(name) is None]
    return {'ready':not missing,'missing':missing,'interpreter':sys.executable}

def create_server(db=DEFAULT_DB,port=8765):
    db=Path(db)
    if not db.exists():raise FileNotFoundError('缺少 data/catalog.sqlite，请完整解压项目。')
    from .evidence_review import ensure_review_schema
    ensure_review_schema(db)
    log_dir=PROJECT/'outputs/logs';log_dir.mkdir(parents=True,exist_ok=True)
    logger=logging.getLogger('ec_agent.http')
    if not logger.handlers:
        handler=RotatingFileHandler(log_dir/'server.log',maxBytes=1_000_000,backupCount=2,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'));logger.addHandler(handler)
    logger.setLevel(logging.ERROR);logger.propagate=False
    class Handler(BaseHTTPRequestHandler):
        def send(self,value,status=200,content_type='application/json; charset=utf-8'):
            body=dumps(value).encode() if isinstance(value,(dict,list)) else value.encode() if isinstance(value,str) else value
            self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(body)))
            self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store')
            self.end_headers();self.wfile.write(body)
        def log_message(self,format,*args):pass
        def model_failure(self,exc):
            value=model_failure_details(exc);value['request_id']=uuid.uuid4().hex[:12]
            logger.error('request=%s path=%s stage=%s code=%s provider_status=%s error=%s',
                value['request_id'],urlparse(self.path).path,value['stage'],value['code'],
                value.get('provider_status','none'),type(exc).__name__)
            return self.send(value,422 if value['stage']=='answer_validation' else 502)
        def failure(self,exc):
            request_id=uuid.uuid4().hex[:12]
            # Never log request bodies, keys, provider responses, or exception messages.
            logger.error('request=%s path=%s error=%s',request_id,urlparse(self.path).path,type(exc).__name__)
            if isinstance(exc,ModuleNotFoundError):
                return self.send({'error':'当前运行环境缺少 '+str(exc.name)+'。请运行 setup.cmd 完成安装，再用 start.cmd 启动。',
                    'code':'missing_dependency','request_id':request_id},503)
            return self.send({'error':'请求未完成，请查看 outputs/logs/server.log。日志编号：'+request_id,
                'code':'request_failed','request_id':request_id},500)
        def do_GET(self):
            try:
                if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):
                    return self.send({'error':'仅接受本地访问'},403)
                u=urlparse(self.path);p=u.path;qs=parse_qs(u.query);q=lambda k,d='':qs.get(k,[d])[0]
                if p=='/':return self.send((Path(__file__).parent/'web/index.html').read_text(encoding='utf-8'),content_type='text/html; charset=utf-8')
                if p in ('/chat.js','/chat.css','/filters.js','/filters.css'):
                    return self.send((Path(__file__).parent/'web'/p[1:]).read_text(encoding='utf-8'),content_type='application/javascript; charset=utf-8' if p.endswith('.js') else 'text/css; charset=utf-8')
                if p=='/api/health':return self.send({'ok':True,'application':'ec-catalog-agent','version':__version__,'agent_count':1,'workflows':['structure','neighbors','ecology'],'mode':'local','llm_ready':public_settings()['configured'],'environment':environment_status()})
                if p=='/api/model-settings':return self.send(public_settings())
                if p=='/api/export':
                    key=q('kind');allowed={'entities':'entities.csv','clusters':'clusters.csv','summary':'run_summary.json','triage':'triage.json'}
                    if key not in allowed:return self.send({'error':'Unknown export'},400)
                    if key=='entities':
                        buf=io.StringIO(newline='');writer=csv.writer(buf)
                        with connect(db) as con:
                            cur=con.execute('''SELECT e.id,e.name,e.original_name,e.cas,e.category,e.subcategory,e.entity_type,e.experiment_candidate,e.measured,e.human_review,e.identity_status,e.source_record_ids,s.inchikey,s.smiles,s.formula,s.molecular_weight,s.features_json,s.clustering_eligible,s.parent_key FROM entities e LEFT JOIN structures s ON s.id=e.selected_structure_id ORDER BY e.id''')
                            writer.writerow([c[0] for c in cur.description])
                            for row in cur:
                                writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for v in row])
                        return self.send(('\ufeff'+buf.getvalue()).encode('utf-8'),content_type='text/csv; charset=utf-8')
                    if key=='triage':
                        with connect(db) as con:return self.send(first_pass(con))
                    file=PROJECT/'outputs'/allowed[key]
                    if not file.exists():file=PROJECT/'reference_outputs'/allowed[key]
                    if not file.exists():return self.send({'error':'请先完成全量工作流'},404)
                    return self.send(file.read_bytes(),content_type='text/csv; charset=utf-8' if file.suffix=='.csv' else 'application/json; charset=utf-8')
                with connect(db) as con:
                    if p.startswith('/api/evidence/'):
                        eid=p.removeprefix('/api/evidence/')
                        if not con.execute('SELECT 1 FROM entities WHERE id=?',(eid,)).fetchone():return self.send({'error':'实体不存在'},404)
                        if q('format')=='csv':
                            buf=io.StringIO(newline='');writer=csv.writer(buf)
                            cur=con.execute('''SELECT v.*,s.url source_url,s.version source_version,s.metadata_json source_metadata FROM reviewed_evidence v
                                JOIN sources s ON s.id=v.source_id WHERE v.entity_id=? ORDER BY v.id''',(eid,))
                            writer.writerow([c[0] for c in cur.description])
                            for row in cur:writer.writerow(["'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v for v in row])
                            self.send_response(200);body=('\ufeff'+buf.getvalue()).encode('utf-8')
                            self.send_header('Content-Type','text/csv; charset=utf-8');self.send_header('Content-Disposition',f'attachment; filename="{eid}-evidence.csv"')
                            self.send_header('Content-Length',str(len(body)));self.send_header('X-Content-Type-Options','nosniff');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body);return
                        return self.send(evidence_page(con,eid,page=int(q('page','1')),limit=int(q('limit','40'))))
                    if p=='/api/stats':return self.send(stats(con))
                    if p=='/api/entities':return self.send(search(con,**{k:q(k) for k in ('q','run_id','cluster','group')},**{k:qs.get(k,[]) for k in ('category','experiment','measured','structure')},page=int(q('page','1')),limit=int(q('limit','50'))))
                    if p=='/api/triage':return self.send(first_pass(con))
                    if p=='/api/clusters':
                        run=q('run_id')
                        rows=con.execute('''SELECT m.cluster_id,m.center_parent_key,count(DISTINCT m.parent_key) parent_count,count(DISTINCT e.id) entity_count,
                         min(CASE WHEN m.parent_key=m.center_parent_key THEN e.name END) center_name,
                         min(m.center_similarity) min_center_similarity
                         FROM cluster_members m JOIN structures s ON s.parent_key=m.parent_key JOIN entities e ON e.selected_structure_id=s.id
                         WHERE m.run_id=? GROUP BY m.cluster_id ORDER BY parent_count DESC,m.cluster_id''',(run,)).fetchall()
                        return self.send({'run_id':run,'clusters':[dict(r) for r in rows]})
                    if p.startswith('/api/entity/'):
                        eid=p.removeprefix('/api/entity/');record=detail(con,eid)
                        if record is None:return self.send({'error':'实体不存在'},404)
                        record['neighbors']=nearest(con,eid,10);record['risk']=risk_results(con,eid)
                        return self.send(record)
                    if p.startswith('/api/structure/'):
                        from rdkit import Chem
                        from rdkit.Chem.Draw import rdMolDraw2D
                        sid=p.removeprefix('/api/structure/').removesuffix('.svg')
                        row=con.execute('SELECT smiles FROM structures WHERE id=?',(sid,)).fetchone()
                        mol=Chem.MolFromSmiles(row[0]) if row else None
                        if mol is None:return self.send({'error':'无可显示的结构'},404)
                        drawer=rdMolDraw2D.MolDraw2DSVG(650,340);drawer.DrawMolecule(mol);drawer.FinishDrawing()
                        return self.send(drawer.GetDrawingText(),content_type='image/svg+xml')
                return self.send({'error':'Not found'},404)
            except (ValueError,TypeError):return self.send({'error':'请求参数无效'},400)
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception as exc:return self.failure(exc)
        def do_POST(self):
            # Loopback only, JSON only, same-origin writes. No arbitrary file or SQL interface.
            host=self.headers.get('Host')
            if host not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):
                return self.send({'error':'仅接受本地访问'},403)
            expected='http://'+host
            if self.headers.get('Origin')!=expected:return self.send({'error':'仅接受本地页面操作'},403)
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':return self.send({'error':'仅接受JSON'},415)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if length<1 or length>16000:return self.send({'error':'请求大小无效'},400)
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict):return self.send({'error':'请求内容必须是 JSON 对象'},400)
                if self.path=='/api/model-settings':
                    try:return self.send(save_settings(data))
                    except ValueError as exc:return self.send({'error':str(exc)},400)
                if self.path=='/api/model-test':
                    try:return self.send(test_connection())
                    except ValueError as exc:return self.send({'error':str(exc)},400)
                    except Exception as exc:return self.model_failure(exc)
                if self.path=='/api/analyze':
                    query=str(data.get('query','')).strip()
                    if not query or len(query)>200:return self.send({'error':'请输入200字以内的名称、CAS或实体ID'},400)
                    from .analysis_flow import run_entity_analysis
                    workflows=data.get('workflows')
                    if workflows is not None and not isinstance(workflows,list):return self.send({'error':'workflows必须是流程名称列表'},400)
                    return self.send(run_entity_analysis(query,db,workflows=workflows))
                if self.path=='/api/review':
                    eid=data['entity_id'];decision=data['decision'];note=str(data.get('note','')).strip()
                    if decision not in ('confirmed','needs_correction','unreviewed') or not note:return self.send({'error':'请选择状态并填写核对依据'},400)
                    with connect(db) as con:
                        if not con.execute('SELECT 1 FROM entities WHERE id=?',(eid,)).fetchone():return self.send({'error':'实体不存在'},404)
                        con.execute('UPDATE entities SET human_review=?,updated_at=? WHERE id=?',(decision,now(),eid))
                        con.execute('INSERT INTO review_events(entity_id,created_at,decision,note) VALUES(?,?,?,?)',(eid,now(),decision,note[:4000]))
                    return self.send({'ok':True})
                if self.path=='/api/ask':
                    question=str(data.get('question','')).strip()
                    if not question or len(question)>2000:return self.send({'error':'问题为空或过长'},400)
                    help_reply=local_help(question)
                    if help_reply is not None:return self.send(help_reply)
                    if not public_settings()['configured']:return self.send({'error':'请先在“模型设置”中选择接口并保存 API Key。固定分析无需模型。','code':'model_not_configured'},409)
                    from .crew import answer_question
                    try:return self.send(answer_question(question,db,context_entity_id=data.get('context_entity_id')))
                    except ConversationContextError as exc:return self.send({'error':str(exc),'code':'invalid_context'},400)
                    except ModuleNotFoundError:raise
                    except Exception as exc:return self.model_failure(exc)
                return self.send({'error':'Not found'},404)
            except (ValueError,KeyError,TypeError):return self.send({'error':'请求内容无效'},400)
            except (BrokenPipeError,ConnectionResetError):pass
            except Exception as exc:return self.failure(exc)
    return ThreadingHTTPServer(('127.0.0.1',int(port)),Handler)

def serve(db=DEFAULT_DB,port=8765):
    try:server=create_server(db,port)
    except OSError:
        import urllib.request
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{int(port)}/api/health',timeout=3) as response:
                health=json.load(response)
            if health.get('application')=='ec-catalog-agent' and health.get('version')==__version__:
                print(f'Catalog already running: http://127.0.0.1:{int(port)}',flush=True);return
        except Exception:pass
        raise
    print(f'Catalog ready: http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
