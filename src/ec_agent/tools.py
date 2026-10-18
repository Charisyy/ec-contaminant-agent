"""CrewAI tool boundary: typed arguments, bounded catalog access, JSON results."""
from __future__ import annotations
import hashlib,json
from typing import Literal
from pydantic import BaseModel,Field,ConfigDict,PrivateAttr
from crewai.tools import BaseTool
from .db import DEFAULT_DB,PROJECT,connect_readonly,detail,search,dumps
from .chemistry import analyze_smiles,analyze_all,cluster_all,nearest
from .evidence import risk_results,import_epa
from .policy import POLICY,WorkflowRequest,method_metadata

class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')
class EntityInput(Input):
    entity_id:str=Field(pattern=r'^EC\d{4}$',description='Exact catalog entity ID, e.g. EC0872')
class SearchInput(Input):
    query:str=Field(min_length=1,max_length=200,description='Chemical name, CAS or entity ID')
class SimilarInput(EntityInput):
    limit:int=Field(default=5,ge=1,le=20)
class ClusterInput(EntityInput):
    distance_threshold:Literal[.3,.4,.5]=.4
class BatchClusterInput(Input):
    distance_threshold:Literal[.3,.4,.5]=.4
class EmptyInput(Input):pass

class CatalogTool(BaseTool):
    db_path:str=str(DEFAULT_DB)

class CatalogSearchTool(CatalogTool):
    name:str='catalog_search'
    description:str='Search ALL catalog entities by name, CAS or ID; return total and up to 12 matches. Multiple matches require explicit entity selection. Procurement flags never restrict scope.'
    args_schema:type[BaseModel]=SearchInput
    def _run(self,query:str):
        with connect_readonly(self.db_path) as con:return dumps(search(con,q=query,limit=12))

class StructureEvidenceTool(CatalogTool):
    name:str='entity_structure_and_evidence'
    description:str='Read selected/candidate structures, provenance, review state and sourced ecological evidence for a catalog entity. Database mapping is not confirmed identity.'
    args_schema:type[BaseModel]=EntityInput
    def _run(self,entity_id:str):
        with connect_readonly(self.db_path) as con:
            value=detail(con,entity_id)
            if value:value['risk']=risk_results(con,entity_id)
            return dumps(value or {'status':'not_found','entity_id':entity_id})

class ParseStructureTool(CatalogTool):
    name:str='parse_catalog_structure'
    description:str='Recompute RDKit parsing and descriptors from the catalog selected SMILES; never guess or overwrite a structure. Return missing/candidate status when no selected structure exists.'
    args_schema:type[BaseModel]=EntityInput
    def _run(self,entity_id:str):
        with connect_readonly(self.db_path) as con:
            row=con.execute('''SELECT e.id,e.name,e.human_review,e.entity_type,s.smiles,s.inchikey,s.features_json FROM entities e LEFT JOIN structures s ON s.id=e.selected_structure_id WHERE e.id=?''',(entity_id,)).fetchone()
            if not row:return dumps({'status':'not_found','entity_id':entity_id})
            result=dict(row);result['entity_id']=result.pop('id')
            cached=json.loads(result.pop('features_json') or '{}')
            if not row['smiles']:return dumps({**result,'status':'structure_missing','reason':'No selected structure; do not choose a candidate automatically.'})
            result.update(status='completed',calculation=analyze_smiles(row['smiles']))
            result['status']=result['calculation']['status']
            result['identifier_consistent']=result['calculation'].get('computed_inchikey')==row['inchikey']
            result['cached_rules_version']=cached.get('rules_version')
            result['cache_status']='current' if cached.get('rules_version')==result['calculation'].get('rules_version') else 'historical'
            return dumps(result)

class FunctionalGroupTool(CatalogTool):
    name:str='detect_functional_groups'
    description:str='Execute versioned SMARTS rules on selected catalog structure. Return actual matched atom indices and counts, not toxicity classes.'
    args_schema:type[BaseModel]=EntityInput
    def _run(self,entity_id:str):
        value=json.loads(ParseStructureTool(db_path=self.db_path).run(entity_id=entity_id))
        calculation=value.get('calculation',{})
        return dumps({'entity_id':entity_id,'status':value['status'],'rules_version':calculation.get('rules_version'),
            'functional_groups':calculation.get('functional_groups',{}),'interpretation':'片段规则可能重叠；命中不代表毒性判定。'})

class SimilarityTool(CatalogTool):
    name:str='find_similar_entities'
    description:str='Calculate Morgan/Tanimoto neighbors against ALL applicable catalog entities, with parent policy and actual similarity values. Similarity does not establish toxicity equivalence.'
    args_schema:type[BaseModel]=SimilarInput
    def _run(self,entity_id:str,limit:int=5):
        with connect_readonly(self.db_path) as con:
            row=con.execute('SELECT s.clustering_eligible,s.exclusion_reason,s.smiles,s.parent_key,s.parent_smiles FROM entities e LEFT JOIN structures s ON s.id=e.selected_structure_id WHERE e.id=?',(entity_id,)).fetchone()
            ready=bool(row and row['clustering_eligible'])
            if ready:
                fresh=analyze_smiles(row['smiles'])
                if not fresh.get('eligible') or fresh.get('parent_key')!=row['parent_key'] or fresh.get('parent_smiles')!=row['parent_smiles']:
                    return dumps({'entity_id':entity_id,'status':'cache_mismatch','neighbors':[],
                        'reason':'缓存母体与当前解析不一致；不自动重算数据库或继续比较。'})
            return dumps({'entity_id':entity_id,'status':'completed' if ready else 'not_applicable','scope':'all eligible catalog entities',
                'reason':'' if ready else (row['exclusion_reason'] if row else 'Entity not found'),
                'parameters':method_metadata()['similarity'],
                'parent_policy':method_metadata()['parent_policy'],
                'neighbors':nearest(con,entity_id,limit) if ready else []})

class ClusterMembershipTool(CatalogTool):
    name:str='inspect_structure_clusters'
    description:str='Read the entity cluster from an existing full-catalog Butina run at distance 0.3/0.4/0.5. Return parameters, center similarity and members. Never label structural clusters as risk groups.'
    args_schema:type[BaseModel]=ClusterInput
    def _run(self,entity_id:str,distance_threshold:float=.4):
        with connect_readonly(self.db_path) as con:
            run=con.execute("SELECT * FROM cluster_runs WHERE json_extract(config_json,'$.distance_threshold')=? ORDER BY created_at DESC LIMIT 1",(distance_threshold,)).fetchone()
            if not run:return dumps({'entity_id':entity_id,'status':'run_missing','reason':'没有对应聚类快照；日常分析不会自动重新聚类。'})
            parameters=json.loads(run['config_json'])
            metadata=method_metadata()
            expected={k:metadata['similarity'][k] for k in ('radius','bits','chirality')}
            expected.update(algorithm='Butina',distance='1 - Tanimoto',distance_threshold=distance_threshold,
                reordering=True,rdkit=metadata['rdkit_version'],parent_policy=metadata['parent_policy'])
            differences=[key for key,value in expected.items() if parameters.get(key)!=value]
            parents=sorted({r['parent_key']:r['parent_smiles'] for r in con.execute('''SELECT DISTINCT s.parent_key,s.parent_smiles FROM structures s JOIN entities e ON e.selected_structure_id=s.id WHERE s.clustering_eligible=1''')}.items())
            if hashlib.sha256(dumps(parents).encode()).hexdigest()!=run['input_digest']:differences.append('input_digest')
            member=con.execute('SELECT m.* FROM entities e JOIN structures s ON s.id=e.selected_structure_id JOIN cluster_members m ON m.parent_key=s.parent_key WHERE e.id=? AND m.run_id=?',(entity_id,run['id'])).fetchone()
            if not member:return dumps({'entity_id':entity_id,'status':'not_applicable','reason':'No applicable selected parent in this run.'})
            members=[dict(r) for r in con.execute('''SELECT e.id,e.name,m.center_similarity FROM cluster_members m JOIN structures s ON s.parent_key=m.parent_key JOIN entities e ON e.selected_structure_id=s.id WHERE m.run_id=? AND m.cluster_id=? ORDER BY e.id''',(run['id'],member['cluster_id']))]
            return dumps({'entity_id':entity_id,'status':'historical' if differences else 'completed','run_id':run['id'],'parameters':parameters,
                'created_at':run['created_at'],'input_digest':run['input_digest'],
                'cache_status':'historical' if differences else 'current','mismatched_fields':differences,
                'reason':'历史聚类快照，仅供查阅；不与当前结果混用。' if differences else '',
                'membership':dict(member),'member_count':len(members),'members':members[:50],'truncated':len(members)>50})

class EcologicalScreenTool(CatalogTool):
    name:str='screen_ecological_evidence'
    description:str='Read sourced ecological endpoints and EXECUTE prerequisite-checked per-endpoint risk screening. Return blocked reasons when identity/exposure/comparability is missing. EPA benchmark is not PNEC or LC50.'
    args_schema:type[BaseModel]=EntityInput
    def _run(self,entity_id:str):
        with connect_readonly(self.db_path) as con:
            if not con.execute('SELECT 1 FROM entities WHERE id=?',(entity_id,)).fetchone():
                return dumps({'entity_id':entity_id,'status':'not_found','evidence':[],'risk_score':None})
            evidence=[dict(r) for r in con.execute('''SELECT v.id,v.evidence_type,v.endpoint,v.compartment,v.taxon,v.duration_class,v.relation,v.value,v.unit,v.source_cas,v.chemical_form,v.identity_confirmed,v.original_text,s.url source_url,s.version source_version,s.accessed_at,s.content_sha256 FROM evidence v JOIN sources s ON s.id=v.source_id WHERE v.entity_id=? ORDER BY v.id''',(entity_id,))]
            return dumps({'entity_id':entity_id,'status':'completed','evidence':evidence,'risk':risk_results(con,entity_id),'risk_score':None})

class RunResearchWorkflowTool(CatalogTool):
    name:str='run_research_workflow'
    description:str=('Run fixed read-only workflows for ONE catalog query. Choose structure (identity/functional groups), '
        'neighbors (similarity/existing clusters), ecology (sourced evidence/gaps), or a combination. '
        'Multiple matches require selection. Scientific parameters are fixed; no database writes, prediction, or batch jobs.')
    args_schema:type[BaseModel]=WorkflowRequest
    output_dir:str=str(PROJECT/'outputs/analyses')
    _observations:list[dict]=PrivateAttr(default_factory=list)

    @property
    def observations(self):return list(self._observations)

    def _run(self,query:str,workflows:list[str]|None=None):
        from .analysis_flow import run_entity_analysis
        request=WorkflowRequest(query=query,**({'workflows':workflows} if workflows is not None else {}))
        pending=next((r for r in self._observations if r['status']=='needs_entity_selection'),None)
        if pending is not None:return dumps(pending)
        result=run_entity_analysis(request.query,self.db_path,output_dir=self.output_dir,workflows=request.workflows)
        self._observations.append(result)
        return dumps(result)

# Write operations are fixed batch-Flow steps, not free-form question tools.
class AnalyzeCatalogTool(CatalogTool):
    name:str='analyze_full_catalog_structures'
    description:str='Recompute structure descriptors and functional groups for every stored structure. Keep all catalog entities.'
    args_schema:type[BaseModel]=EmptyInput
    def _run(self):return dumps(analyze_all(self.db_path))

class FullCatalogClusteringTool(CatalogTool):
    name:str='cluster_full_catalog'
    description:str='Execute Butina on ALL eligible selected catalog parents, including entities outside procurement candidates; persist reproducible run and members.'
    args_schema:type[BaseModel]=BatchClusterInput
    def _run(self,distance_threshold:float=.4):return dumps(cluster_all(self.db_path,distance_threshold))

class AttachBenchmarksTool(CatalogTool):
    name:str='attach_epa_benchmarks'
    description:str='Import the configured versioned EPA benchmark snapshot by exact CAS; preserve qualifiers and sources.'
    args_schema:type[BaseModel]=EmptyInput
    snapshot_path:str=str(PROJECT/'inputs/epa_benchmarks.json')
    def _run(self):return dumps(import_epa(self.snapshot_path,self.db_path))
