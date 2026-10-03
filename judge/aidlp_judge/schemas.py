"""Strict public contracts; model findings never supply trusted identity."""
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class Action(StrEnum):
    ALLOW = 'allow'
    BLOCK = 'block'
    REDACT = 'redact'
    REVIEW = 'review'

class Policy(StrictModel):
    id: str = Field(pattern=r'^[a-z][a-z0-9_-]{1,63}$')
    name: str = Field(min_length=2, max_length=100)
    enabled: bool = True
    scenarios: list[str] = Field(min_length=1, max_length=6)
    question: str = Field(min_length=10, max_length=1500)
    match_description: str = Field(min_length=5, max_length=700)
    no_match_description: str = Field(min_length=5, max_length=700)
    effect: Literal['block', 'review'] = 'block'
    threshold: float = Field(ge=0.5, le=0.999, default=0.85)
    uncertainty_action: Literal['block', 'review'] = 'review'
    destinations: list[Literal['personal_ai','approved_ai','external_mail','internal_ai']] = Field(min_length=1)
    model_id: Literal['decider-2b','decider-4b','standardone-3b'] = 'decider-4b'

class PolicyPublish(StrictModel):
    expected_revision: int = Field(ge=1)
    policies: list[Policy] = Field(min_length=1, max_length=20)
    @model_validator(mode='after')
    def unique_ids(self):
        if len({p.id for p in self.policies}) != len(self.policies):
            raise ValueError('duplicate_policy_id')
        return self

SCENARIO_IDS = ('customer_upload','m365_grounding','drive_personal','cloud_summary','rag_acl','agent_mail')
class RunRequest(StrictModel):
    scenario_id: Literal['customer_upload','m365_grounding','drive_personal','cloud_summary','rag_acl','agent_mail']
    variant: Literal['protected','allowed'] = 'protected'
    text: str | None = Field(default=None, max_length=60000)
    filename: str | None = Field(default=None, max_length=160)
    file_base64: str | None = Field(default=None, max_length=5600000)
    approval_id: str | None = Field(default=None, pattern=r'^[a-f0-9-]{36}$')
    @model_validator(mode='after')
    def consistent_content(self):
        if self.file_base64 is not None and (not self.filename or self.text is not None):
            raise ValueError('file_requires_name_and_no_text')
        if self.filename and self.file_base64 is None:
            raise ValueError('filename_requires_file')
        return self

class Finding(StrictModel):
    policy_id: str
    choice: Literal['match','no_match','insufficient']
    scores: dict[str,float]
    model_id: str
    model_revision: str
    runtime: str
    input_tokens: int
    latency_ms: float
    cached: bool = False

class Decision(StrictModel):
    id: str
    action: Action
    reason: str
    policy_revision: int
    request_digest: str
    scenario_id: str
    evidence_scope: Literal['controlled_local_adapter'] = 'controlled_local_adapter'
    findings: list[Finding] = Field(default_factory=list)
    detector_ids: list[str] = Field(default_factory=list)
    approval_id: str | None = None
    delivered: bool = False
    delivery_id: str | None = None
    extraction: dict = Field(default_factory=dict)
    elapsed_ms: float = 0
