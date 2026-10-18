"""Researcher-owned method configuration, never supplied by an LLM."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

WorkflowId = Literal['structure', 'neighbors', 'ecology']
POLICY_FILE = Path(__file__).with_name('research_policy.json')
POLICY = json.loads(POLICY_FILE.read_text(encoding='utf-8'))
POLICY_SHA256 = hashlib.sha256(POLICY_FILE.read_bytes()).hexdigest()


class WorkflowRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    query: str = Field(min_length=1, max_length=200)
    workflows: list[WorkflowId] = Field(
        default_factory=lambda: list(POLICY['default_workflows']), min_length=1, max_length=3)

    @field_validator('query')
    @classmethod
    def clean_query(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('检索对象不能为空')
        return value

    @field_validator('workflows')
    @classmethod
    def canonical_workflows(cls, values):
        if len(values) != len(set(values)):
            raise ValueError('不能重复选择同一工作流')
        return [key for key in POLICY['workflows'] if key in values]


def method_metadata():
    from .chemistry import PARENT_POLICY, RULES_VERSION
    import rdkit
    return {
        'policy_version': POLICY['version'], 'policy_sha256': POLICY_SHA256,
        'rdkit_version': rdkit.__version__, 'rules_version': RULES_VERSION,
        'rulebook_sha256': hashlib.sha256(
            Path(__file__).with_name('functional_group_rules.json').read_bytes()).hexdigest(),
        'parent_policy': PARENT_POLICY,
        'similarity': dict(POLICY['similarity']), 'clustering': dict(POLICY['clustering']),
        'prediction_enabled': False,
    }
