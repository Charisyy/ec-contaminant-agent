"""Offline acceptance only: no model configuration or network calls."""
import hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from ec_agent.db import DEFAULT_DB
from ec_agent.chemistry import analyze_smiles,RULES_VERSION
from ec_agent.analysis_flow import run_entity_analysis

def main():
    output=ROOT/'outputs/acceptance';output.mkdir(parents=True,exist_ok=True)
    digest=lambda:hashlib.sha256(DEFAULT_DB.read_bytes()).hexdigest()
    before=digest();rules=json.loads((ROOT/'validation/functional_group_cases.json').read_text(encoding='utf-8'))
    mismatches=[]
    for case in rules['cases']:
        result=analyze_smiles(case['smiles']);count=result.get('functional_groups',{}).get(case['rule'],{}).get('count',0)
        if result['status']!='parsed' or count!=case['expected_matches']:mismatches.append({'case':case['id'],'expected':case['expected_matches'],'actual':count})
    suite=json.loads((ROOT/'validation/model_acceptance_cases.json').read_text(encoding='utf-8'))
    checks=[]
    for case in suite['cases']:
        result=run_entity_analysis(case['query'],output_dir=output/'workflow_runs',workflows=case['workflows'])
        ok=result['status']==case['status'] and result['entity_id']==case['entity_id'] and result['risk_score'] is None
        if 'structure_status' in case:ok &= result['results']['structure']['status']==case['structure_status']
        count=len(result['results'].get('ecology',{}).get('evidence',[]))
        if 'minimum_evidence' in case:ok &= count>=case['minimum_evidence']
        if 'expected_evidence' in case:ok &= count==case['expected_evidence']
        checks.append({'case':case['id'],'passed':bool(ok),'status':result['status'],'entity_id':result['entity_id'],'evidence_records':count,'artifact':result['artifact_name']})
    report={'passed':not mismatches and all(c['passed'] for c in checks) and before==digest(),
        'rules_version':RULES_VERSION,'reference_cases':len(rules['cases']),'rule_mismatches':mismatches,
        'known_boundary_cases':sum(c['kind']=='boundary' for c in rules['cases']),
        'scientific_review_status':rules['review_status'],'workflow_checks':checks,
        'real_model_status':'not_run','external_model_requests':0,'database_unchanged':before==digest()}
    (output/'offline_acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'reference_cases':report['reference_cases'],'workflow_cases':len(checks),'real_model_status':'not_run','report':'outputs/acceptance/offline_acceptance.json'}))
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
