from __future__ import annotations
import hashlib,json,math
from array import array
from pathlib import Path
import rdkit
from rdkit import Chem,DataStructs,RDLogger
from rdkit.Chem import Descriptors,Crippen,Lipinski,rdMolDescriptors,rdFingerprintGenerator
from rdkit.ML.Cluster import Butina
from .db import connect,dumps,now

RDLogger.DisableLog('rdApp.warning')
RULEBOOK=json.loads(Path(__file__).with_name('functional_group_rules.json').read_text(encoding='utf-8'))
RULES_VERSION=RULEBOOK['version']
RULES={r['name']:r['smarts'] for r in RULEBOOK['rules']}
RULE_DEFINITIONS={r['name']:r['definition'] for r in RULEBOOK['rules']}
if len(RULES)!=len(RULEBOOK['rules']):raise ValueError('Duplicate functional group rule names')
QUERY={k:Chem.MolFromSmarts(v) for k,v in RULES.items()}
if any(q is None for q in QUERY.values()):raise ValueError('Invalid functional group SMARTS')
PARENT_POLICY='largest carbon-containing fragment; tie-break by canonical isomeric SMILES; retain charge, tautomers and stereo'
ALLOWED={1,5,6,7,8,9,14,15,16,17,34,35,53}

def analyze_smiles(smiles):
    if not isinstance(smiles,str) or not smiles.strip():
        return {'status':'failed','eligible':False,'reason':'SMILES为空或不是文本','rules_version':RULES_VERSION}
    parser=Chem.SmilesParserParams()
    parser.parseName=False
    mol=Chem.MolFromSmiles(smiles,parser)
    if mol is None: return {'status':'failed','eligible':False,'reason':'SMILES解析失败','rules_version':RULES_VERSION}
    if not mol.GetNumAtoms():return {'status':'failed','eligible':False,'reason':'SMILES中没有原子','rules_version':RULES_VERSION}
    fragments=Chem.GetMolFrags(mol,asMols=True,sanitizeFrags=True)
    carbons=lambda m:sum(a.GetAtomicNum()==6 for a in m.GetAtoms())
    pool=[m for m in fragments if carbons(m)] or list(fragments)
    parent=sorted(pool,key=lambda m:(-carbons(m),-m.GetNumHeavyAtoms(),Chem.MolToSmiles(m,isomericSmiles=True)))[0]
    parent_smiles=Chem.MolToSmiles(parent,isomericSmiles=True)
    parent_key=Chem.MolToInchiKey(parent)
    atomnums={a.GetAtomicNum() for a in parent.GetAtoms()}
    eligible=6 in atomnums and atomnums.issubset(ALLOWED)
    reason='' if eligible else ('无碳无机物：保留结构，单独评价' if 6 not in atomnums else '含金属/特殊元素：保留结构，单独评价')
    if len([m for m in fragments if carbons(m)])>1:
        eligible=False;reason='多个含碳片段：需人工确认母体后再聚类'
    groups={}
    for name,query in QUERY.items():
        matches=mol.GetSubstructMatches(query,uniquify=True)
        if matches: groups[name]={'count':len(matches),'atom_matches':[list(m) for m in matches],
            'kind':'substructure','smarts':RULES[name],'definition':RULE_DEFINITIONS[name]}
    aromatic=[a.GetIdx() for a in mol.GetAtoms() if a.GetIsAromatic()]
    if aromatic: groups['芳香原子']={'count':len(aromatic),'atom_indices':aromatic,
        'kind':'atom_feature','definition':RULEBOOK['atom_features'][0]['definition']}
    mw=Descriptors.MolWt(mol)
    flags=[]
    if len(fragments)>1: flags.append('母体指纹使用最大含碳片段；原结构和盐型完整保留')
    if mw>1000: flags.append('大于1000 g/mol：分子量偏大，请核查所选结构与实体类型')
    if Chem.GetFormalCharge(mol): flags.append('带电结构：logP估计不是指定pH下logD')
    return {'status':'parsed','eligible':eligible,'reason':reason,'parent_key':parent_key,'parent_smiles':parent_smiles,
      'rdkit_version':rdkit.__version__,'rules_version':RULES_VERSION,'parent_policy':PARENT_POLICY,
      'input_smiles':smiles,'atom_indexing':RULEBOOK['atom_indexing'],
      'canonical_smiles':Chem.MolToSmiles(mol,isomericSmiles=True),'computed_inchikey':Chem.MolToInchiKey(mol),
      'formula':rdMolDescriptors.CalcMolFormula(mol),'molecular_weight':mw,'exact_mass':rdMolDescriptors.CalcExactMolWt(mol),
      'estimated_logp':Crippen.MolLogP(mol),'tpsa':rdMolDescriptors.CalcTPSA(mol),
      'hbd':Lipinski.NumHDonors(mol),'hba':Lipinski.NumHAcceptors(mol),'rotatable_bonds':Lipinski.NumRotatableBonds(mol),
      'formal_charge':Chem.GetFormalCharge(mol),'fragment_count':len(fragments),'heavy_atoms':mol.GetNumHeavyAtoms(),
      'functional_groups':groups,'flags':flags}

def analyze_all(db):
    counts={'parsed':0,'failed':0,'eligible_unique_structures':0}
    with connect(db) as con:
        rows=con.execute('SELECT * FROM structures ORDER BY id').fetchall()
        for row in rows:
            try:result=analyze_smiles(row['smiles'])
            except Exception as exc:result={'status':'failed','eligible':False,'reason':'结构计算失败：'+type(exc).__name__}
            counts[result['status']]+=1
            counts['eligible_unique_structures']+=int(result['eligible'])
            con.execute('UPDATE structures SET features_json=?,parent_key=?,parent_smiles=?,clustering_eligible=?,analysis_status=?,exclusion_reason=? WHERE id=?',
             (dumps(result),result.get('parent_key'),result.get('parent_smiles'),int(result['eligible']),result['status'],result['reason'],row['id']))
    return counts

def cluster_all(db, distance_threshold=.4):
    if not 0<distance_threshold<1: raise ValueError('Distance threshold must be between zero and one')
    with connect(db) as con:
        rows=con.execute('SELECT DISTINCT s.parent_key,s.parent_smiles FROM structures s JOIN entities e ON e.selected_structure_id=s.id WHERE s.clustering_eligible=1 ORDER BY s.parent_key').fetchall()
        parents=sorted({r['parent_key']:r['parent_smiles'] for r in rows}.items())
        config={'algorithm':'Butina','distance':'1 - Tanimoto','distance_threshold':distance_threshold,'radius':2,'bits':2048,'chirality':True,'reordering':True,'rdkit':rdkit.__version__,'parent_policy':PARENT_POLICY,'scope':'all eligible catalog entities','interpretation':'center similarity threshold; not an all-pairs guarantee'}
        digest=hashlib.sha256(dumps(parents).encode()).hexdigest()
        run_id=hashlib.sha256((digest+dumps(config)).encode()).hexdigest()[:20]
        fpgen=rdFingerprintGenerator.GetMorganGenerator(radius=2,fpSize=2048,includeChirality=True)
        fps=[fpgen.GetFingerprint(Chem.MolFromSmiles(s)) for _,s in parents]
        # Butina consumes the lower triangular distance vector in this order.
        distances=[]
        for i in range(1,len(fps)): distances.extend(1-v for v in DataStructs.BulkTanimotoSimilarity(fps[i],fps[:i]))
        clusters=Butina.ClusterData(distances,len(fps),distance_threshold,isDistData=True,reordering=True) if fps else []
        clusters=sorted(clusters,key=lambda c:(-len(c),parents[c[0]][0]))
        con.execute('INSERT OR IGNORE INTO cluster_runs VALUES(?,?,?,?,?,?)',(run_id,dumps(config),digest,now(),len(parents),len(clusters)))
        con.execute('DELETE FROM cluster_members WHERE run_id=?',(run_id,))
        for i,cluster in enumerate(clusters,1):
            center=cluster[0]
            for member in cluster:
                con.execute('INSERT INTO cluster_members VALUES(?,?,?,?,?)',(run_id,f'C{i:04d}',parents[member][0],parents[center][0],DataStructs.TanimotoSimilarity(fps[center],fps[member])))
    return {'run_id':run_id,'parents':len(parents),'clusters':len(clusters),'distance_threshold':distance_threshold,'singleton_clusters':sum(len(c)==1 for c in clusters)}

def nearest(con, entity_id, limit=10):
    source=con.execute('SELECT s.* FROM entities e JOIN structures s ON s.id=e.selected_structure_id WHERE e.id=?',(entity_id,)).fetchone()
    if not source or not source['clustering_eligible']: return []
    generator=rdFingerprintGenerator.GetMorganGenerator(radius=2,fpSize=2048,includeChirality=True)
    fp=generator.GetFingerprint(Chem.MolFromSmiles(source['parent_smiles']))
    rows=con.execute('SELECT e.id,e.name,e.cas,s.parent_smiles,s.parent_key FROM entities e JOIN structures s ON s.id=e.selected_structure_id WHERE s.clustering_eligible=1 AND e.id<>?',(entity_id,)).fetchall()
    result=[]
    for r in rows:
        similarity=DataStructs.TanimotoSimilarity(fp,generator.GetFingerprint(Chem.MolFromSmiles(r['parent_smiles'])))
        result.append({'id':r['id'],'name':r['name'],'cas':r['cas'],'similarity':similarity,'same_parent':r['parent_key']==source['parent_key']})
    return sorted(result,key=lambda r:(-r['similarity'],r['id']))[:limit]
