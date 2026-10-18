"""Three fixed research workflows; optional one-agent review of saved results."""
from __future__ import annotations
import hashlib,json,time
from pathlib import Path
from pydantic import BaseModel,Field
from crewai.flow.flow import Flow,start,listen,router
from .db import PROJECT,DEFAULT_DB,dumps,now
from .policy import POLICY,WorkflowRequest,method_metadata
from .tools import (CatalogSearchTool,StructureEvidenceTool,ParseStructureTool,
    FunctionalGroupTool,SimilarityTool,ClusterMembershipTool,EcologicalScreenTool)

class AnalysisState(BaseModel):
    db_path:str=str(DEFAULT_DB)
    output_dir:str=str(PROJECT/'outputs/analyses')
    query:str=''
    entity_id:str=''
    workflows:list[str]=Field(default_factory=lambda:list(POLICY['default_workflows']))
    llm_review:bool=False
    trace:list[dict]=Field(default_factory=list)
    skipped_steps:list[dict]=Field(default_factory=list)
    results:dict=Field(default_factory=dict)
    selection:dict=Field(default_factory=dict)
    workflow_statuses:dict=Field(default_factory=dict)
    warnings:list[str]=Field(default_factory=list)

class EntityAnalysisFlow(Flow[AnalysisState]):
    def call_tool(self,cls,**arguments):
        tool=cls(db_path=self.state.db_path);started=time.perf_counter()
        try:
            result=json.loads(tool.run(**arguments))
            if not isinstance(result,dict):raise TypeError('Expected a JSON object')
        except Exception as exc:
            result={'status':'failed','error_type':type(exc).__name__,
                'reason':'工具执行失败；保留已完成结果，不推断缺失值。'}
        self.state.trace.append({'step':len(self.state.trace)+1,'tool':tool.name,'arguments':arguments,
            'status':result.get('status','completed'),'executed':True,
            'elapsed_ms':round((time.perf_counter()-started)*1000,2),
            'output_sha256':hashlib.sha256(dumps(result).encode()).hexdigest()})
        return result

    @start()
    def resolve_entity(self):
        request=WorkflowRequest(query=self.state.query,workflows=self.state.workflows)
        self.state.query,self.state.workflows=request.query,request.workflows
        matches=self.call_tool(CatalogSearchTool,query=request.query)
        self.state.selection=matches
        if matches.get('status')=='failed':return matches
        exact=[r for r in matches['rows'] if r['id']==request.query.upper()]
        if exact:self.state.entity_id=exact[0]['id']
        elif matches['total']==1:self.state.entity_id=matches['rows'][0]['id']
        return matches

    @router(resolve_entity)
    def route_identity(self):
        return 'resolved' if self.state.entity_id else 'choose_entity'

    @listen('choose_entity')
    def request_entity_selection(self):
        if self.state.selection.get('status')=='failed':return self.finish('failed')
        return self.finish('needs_entity_selection' if self.state.selection['total'] else 'not_found')

    def skip(self,key,cls,reason,**empty_fields):
        value={'entity_id':self.state.entity_id,'status':'not_applicable','reason':reason,**empty_fields}
        self.state.results[key]=value
        self.state.skipped_steps.append({'tool':cls.model_fields['name'].default,'executed':False,'reason':reason})
        return value

    def structure_gate(self,record):
        if record.get('status') in ('failed','not_found'):return '身份与来源查询失败，暂不执行结构分支。'
        if record.get('human_review')=='needs_correction':return '身份记录标记为需更正，暂不执行结构分支。'
        if record.get('entity_type')!='单体/命名实体':return '材料、混合物或其他非单分子实体：保留目录和证据，不直接执行分子工作流。'
        if not record.get('selected_structure_id'):return '没有选定的单一结构；保留候选，不自动选择第一条。'
        return ''

    def run_structure_workflows(self,record):
        reason=self.structure_gate(record)
        if reason:
            self.skip('structure',ParseStructureTool,reason,name=record.get('name'),human_review=record.get('human_review'))
        else:
            parsed=self.call_tool(ParseStructureTool,entity_id=self.state.entity_id)
            self.state.results['structure']=parsed
            if parsed.get('status')!='parsed':reason=parsed.get('reason','结构无法解析，跳过依赖结构的步骤。')
            elif not parsed.get('identifier_consistent'):reason='所选结构与存储的 InChIKey 不一致，需核查后再执行下游分析。'
            if parsed.get('cache_status')=='historical':
                self.state.warnings.append('本次使用规则 '+method_metadata()['rules_version']+' 重新计算；数据库官能团缓存为历史版本，未覆盖或混用。')
        ready=not reason
        if 'structure' in self.state.workflows:
            if ready:
                value=self.call_tool(FunctionalGroupTool,entity_id=self.state.entity_id)
                self.state.results['functional_groups']=value
                self.state.workflow_statuses['structure']='completed' if value.get('status')=='parsed' else 'failed'
            else:
                self.skip('functional_groups',FunctionalGroupTool,reason,functional_groups={})
                self.state.workflow_statuses['structure']='failed' if self.state.results['structure']['status']=='failed' else 'not_applicable'
        if 'neighbors' in self.state.workflows:
            eligible=ready and self.state.results['structure'].get('calculation',{}).get('eligible')
            if eligible:
                self.state.results['similarity']=self.call_tool(SimilarityTool,entity_id=self.state.entity_id,limit=POLICY['similarity']['default_limit'])
                self.state.results['cluster']=self.call_tool(ClusterMembershipTool,entity_id=self.state.entity_id,distance_threshold=POLICY['clustering']['distance_threshold'])
                states=[self.state.results[k].get('status') for k in ('similarity','cluster')]
                self.state.workflow_statuses['neighbors']='completed' if all(s=='completed' for s in states) else 'partial'
            else:
                reason=reason or self.state.results['structure'].get('calculation',{}).get('reason') or '结构不适用于当前母体指纹方案。'
                self.skip('similarity',SimilarityTool,reason,neighbors=[])
                self.skip('cluster',ClusterMembershipTool,reason)
                self.state.workflow_statuses['neighbors']='failed' if self.state.results['structure']['status']=='failed' else 'not_applicable'

    @listen('resolved')
    def execute_selected_workflows(self):
        record=self.call_tool(StructureEvidenceTool,entity_id=self.state.entity_id)
        self.state.results['identity']=record
        if record.get('human_review')!='confirmed':
            self.state.warnings.append('身份尚未人工确认：结构计算以当前选定结构为条件，数据库匹配不等于身份确认。')
        if any(w in self.state.workflows for w in ('structure','neighbors')):self.run_structure_workflows(record)
        if 'ecology' in self.state.workflows:
            value=self.call_tool(EcologicalScreenTool,entity_id=self.state.entity_id)
            self.state.results['ecology']=value
            self.state.workflow_statuses['ecology']='completed' if value.get('status')=='completed' else 'failed'
        if record.get('status')=='failed':self.state.warnings.append('身份与来源工具失败；其他成功查询仍保留，报告不将其视为身份已核实。')
        statuses=list(self.state.workflow_statuses.values())
        status='failed' if statuses and all(s=='failed' for s in statuses) else ('partial' if record.get('status')=='failed' or any(s in ('failed','partial') for s in statuses) else 'completed')
        if self.state.llm_review:
            saved=self.finish(status)
            from .crew import review_analysis
            try:self.state.results['model_review']=review_analysis(saved,self.state.db_path)
            except Exception as exc:
                self.state.results['model_review']={'status':'failed','error_type':type(exc).__name__,'reason':'模型解释未完成；固定工作流结果已经保存。'}
        return self.finish(status)

    def finish(self,status):
        identity=self.state.results.get('identity',{});sources=[]
        for structure in identity.get('structures',[]):
            if structure.get('source_url'):sources.append({'kind':'structure','record_id':structure['id'],'url':structure['source_url']})
        for evidence in identity.get('evidence',[]):
            sources.append({'kind':'evidence','record_id':evidence['id'],'url':evidence.get('source_url'),
                'source_version':evidence.get('source_version'),'accessed_at':evidence.get('accessed_at')})
        for exposure in identity.get('exposures',[]):sources.append({'kind':'exposure','record_id':exposure['id'],'url':exposure.get('source_url')})
        result={'run_id':self.state.id,'created_at':now(),'framework':'CrewAI Flow','status':status,
            'execution_mode':'tools_then_single_agent_review' if self.state.llm_review else 'deterministic_tools',
            'llm_used':'answer' in self.state.results.get('model_review',{}),
            'query':self.state.query,'entity_id':self.state.entity_id or None,
            'workflows':self.state.workflows,'workflow_statuses':self.state.workflow_statuses,
            'selection':{'total':self.state.selection.get('total',0),'choices':[
                {'entity_id':r['id'],'name':r['name'],'cas':r['cas']} for r in self.state.selection.get('rows',[])]},
            'methods':method_metadata(),'provenance':{'identity_status':identity.get('identity_status'),
                'human_review':identity.get('human_review'),'selected_structure_id':identity.get('selected_structure_id'),'sources':sources},
            'warnings':list(dict.fromkeys(self.state.warnings)),'skipped_steps':self.state.skipped_steps,
            'trace':self.state.trace,'results':self.state.results,'risk_score':None}
        output=Path(self.state.output_dir)/self.state.id;output.mkdir(parents=True,exist_ok=True)
        result['artifact_name']=self.state.id+'/result.json'
        (output/'result.json').write_text(dumps(result),encoding='utf-8')
        return result

def run_entity_analysis(query,db=DEFAULT_DB,llm_review=False,output_dir=None,workflows=None):
    request=WorkflowRequest(query=query,**({'workflows':workflows} if workflows is not None else {}))
    flow=EntityAnalysisFlow();flow.state.query=request.query;flow.state.workflows=request.workflows
    flow.state.db_path=str(db);flow.state.llm_review=llm_review
    if output_dir is not None:flow.state.output_dir=str(output_dir)
    return flow.kickoff()
