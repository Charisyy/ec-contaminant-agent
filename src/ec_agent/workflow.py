from __future__ import annotations
import csv,hashlib,json,os
from collections import Counter
from pathlib import Path
os.environ.setdefault('OTEL_SDK_DISABLED','true')
os.environ.setdefault('CREWAI_TELEMETRY_ENABLED','false')
os.environ.setdefault('CREWAI_TRACING_ENABLED','false')
from pydantic import BaseModel,Field
from crewai.flow.flow import Flow,start,listen
from .db import PROJECT,DEFAULT_DB,connect,stats,dumps,now
from .ingest import import_catalog
from .evidence import first_pass
from .tools import AnalyzeCatalogTool,FullCatalogClusteringTool,AttachBenchmarksTool

class CatalogState(BaseModel):
    db_path:str=str(DEFAULT_DB)
    snapshot_path:str=str(PROJECT/'inputs/catalog_snapshot.json')
    benchmarks_path:str=str(PROJECT/'inputs/epa_benchmarks.json')
    output_dir:str=str(PROJECT/'outputs')
    stages:dict=Field(default_factory=dict)

class CatalogFlow(Flow[CatalogState]):
    """A real CrewAI Flow: deterministic tools process the full catalog without an LLM requirement."""
    @start()
    def import_all_entities(self):
        self.state.stages['import']=import_catalog(self.state.snapshot_path,self.state.db_path)
        return self.state.stages['import']

    @listen(import_all_entities)
    def parse_and_detect_groups(self,_):
        self.state.stages['chemistry']=json.loads(AnalyzeCatalogTool(db_path=self.state.db_path).run())
        return self.state.stages['chemistry']

    @listen(parse_and_detect_groups)
    def cluster_full_catalog(self,_):
        self.state.stages['clustering']=[json.loads(FullCatalogClusteringTool(db_path=self.state.db_path).run(distance_threshold=t)) for t in (.3,.4,.5)]
        return self.state.stages['clustering']

    @listen(cluster_full_catalog)
    def attach_source_backed_evidence(self,_):
        self.state.stages['evidence']=json.loads(AttachBenchmarksTool(db_path=self.state.db_path,snapshot_path=self.state.benchmarks_path).run())
        from .literature import import_pack
        pack=PROJECT/'inputs/literature_evidence_20261019.jsonl.gz'
        if pack.exists():self.state.stages['literature']=import_pack(pack,self.state.db_path)
        return self.state.stages['evidence']

    @listen(attach_source_backed_evidence)
    def export_and_validate(self,_):
        output=Path(self.state.output_dir);output.mkdir(parents=True,exist_ok=True)
        with connect(self.state.db_path) as con:
            profile=stats(con);triage=first_pass(con)
            expected=json.loads(Path(self.state.snapshot_path).read_text(encoding='utf-8'))
            assert profile['entities']==len(expected['rows'])
            assert profile['experiment_candidates']==sum(bool(r[0]) for s in expected['source_sheets'] if s['name']=='47种代表物' for r in s['values'][1:])
            assert not con.execute('PRAGMA foreign_key_check').fetchall()
            result={'created_at':now(),'framework':'CrewAI Flow','scope':'ALL_CATALOG_ENTITIES','llm_used':False,'stages':self.state.stages,'statistics':profile,'risk_actions':dict(Counter(r['action'] for r in triage)),
              'limitations':['采购候选和已测是独立标签，不用于限制入库或聚类','数据库匹配不等于全部身份已核实','EPA基准不是PNEC或原始LC50；不同介质和端点不混排','无暴露浓度或身份未确认时不输出风险分数']}
            (output/'run_summary.json').write_text(dumps(result),encoding='utf-8')
            (output/'triage.json').write_text(dumps(triage),encoding='utf-8')
            export_entities(con,output/'entities.csv')
            export_clusters(con,output/'clusters.csv')
            (output/'functional_group_rules.json').write_text(dumps(__import__('ec_agent.chemistry',fromlist=['RULES']).RULES),encoding='utf-8')
            run_id=hashlib.sha256(dumps(result).encode()).hexdigest()[:20]
            con.execute('INSERT OR REPLACE INTO pipeline_runs VALUES(?,?,?,?)',(run_id,now(),'completed',dumps(result)))
        self.state.stages['output']={'summary':str(output/'run_summary.json'),'database':self.state.db_path}
        return result

def safe_cell(value):
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')): return "'"+value
    return value

def export_entities(con,path):
    headers=['entity_id','name','original_name','cas','category','subcategory','entity_type','experiment_candidate','measured','identity_status','human_review','inchikey','smiles','formula','molecular_weight','functional_groups','clustering_eligible','parent_key']
    sql='''SELECT e.id,e.name,e.original_name,e.cas,e.category,e.subcategory,e.entity_type,e.experiment_candidate,e.measured,e.identity_status,e.human_review,s.inchikey,s.smiles,s.formula,s.molecular_weight,s.features_json,s.clustering_eligible,s.parent_key FROM entities e LEFT JOIN structures s ON s.id=e.selected_structure_id ORDER BY e.id'''
    with Path(path).open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(headers)
        for r in con.execute(sql):
            vals=list(r);features=json.loads(vals[15] or '{}');vals[15]=';'.join(features.get('functional_groups',{}))
            writer.writerow([safe_cell(v) for v in vals])

def export_clusters(con,path):
    sql='''SELECT m.run_id,m.cluster_id,m.parent_key,m.center_parent_key,m.center_similarity,e.id,e.name,e.experiment_candidate,e.measured FROM cluster_members m JOIN structures s ON s.parent_key=m.parent_key JOIN entities e ON e.selected_structure_id=s.id ORDER BY m.run_id,m.cluster_id,e.id'''
    with Path(path).open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(['run_id','cluster_id','parent_key','center_parent_key','center_similarity','entity_id','name','experiment_candidate','measured'])
        for row in con.execute(sql):writer.writerow([safe_cell(v) for v in row])
