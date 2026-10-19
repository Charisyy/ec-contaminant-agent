"""Declared rule behavior and boundaries; expected cases are stored independently."""
import json
from ec_agent.db import PROJECT
from ec_agent.chemistry import analyze_smiles,RULES,RULES_VERSION
import pytest

CASES=json.loads((PROJECT/'validation/functional_group_cases.json').read_text(encoding='utf-8'))

@pytest.mark.parametrize('case',CASES['cases'],ids=lambda case:case['id'])
def test_rule_reference_case(case):
    result=analyze_smiles(case['smiles'])
    assert result['status']=='parsed'
    assert result['functional_groups'].get(case['rule'],{}).get('count',0)==case['expected_matches']
    for group in result['functional_groups'].values():
        if group['kind']=='substructure':
            assert all(0<=atom<result['heavy_atoms'] for match in group['atom_matches'] for atom in match)

def test_each_rule_has_both_controls_and_version_is_explicit():
    assert CASES['rules_version']==RULES_VERSION=='1.2'
    for kind in ('positive','negative'):
        assert {case['rule'] for case in CASES['cases'] if case['kind']==kind}==set(RULES)
    assert CASES['review_status']=='researcher_review_pending'
