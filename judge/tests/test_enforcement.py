import base64
import hashlib
from pathlib import Path
import pytest
from aidlp_judge.engine import Engine
from aidlp_judge.store import Store,Conflict
from aidlp_judge.schemas import RunRequest,Policy

class Judge:
    def __init__(self,choice='no_match'):self.choice=choice;self.calls=[]
    def evaluate(self,model,text,question,choices):
        self.calls.append(text)
        return dict(choice=self.choice,scores={k:0.98 if k==self.choice else 0.01 for k in choices},
            model_id=model,model_revision='test-only',runtime='explicit_test_double',input_tokens=10,latency_ms=1)
class Receiver:
    def __init__(self):self.received=[]
    def send(self,digest,scenario,text):
        self.received.append(text)
        return dict(id='receipt-test',content_hash=hashlib.sha256(text.encode()).hexdigest())
@pytest.fixture
def setup(tmp_path):
    s=Store(tmp_path/'test.sqlite');j=Judge();r=Receiver()
    return s,j,r,Engine(s,j,r)

def test_acl_prevents_judge_and_delivery(setup):
    s,j,r,e=setup
    d=e.run(RunRequest(scenario_id='rag_acl'))
    assert d.action=='block' and not j.calls and not r.received

def test_label_prevents_processing(setup):
    s,j,r,e=setup
    d=e.run(RunRequest(scenario_id='m365_grounding'))
    assert d.reason=='label_prohibits_ai_processing' and not j.calls and not r.received

def test_semantic_block_prevents_tool_execution(setup):
    s,j,r,e=setup;j.choice='match'
    d=e.run(RunRequest(scenario_id='agent_mail'))
    assert d.action=='block' and len(j.calls)==1 and not r.received

def test_secret_cannot_be_allowed_by_model(setup):
    s,j,r,e=setup
    d=e.run(RunRequest(scenario_id='agent_mail',text='sk-proj-'+'a'*28))
    assert d.reason=='secret_detected' and not j.calls and not r.received

def test_cloud_redaction_before_receiver(setup):
    s,j,r,e=setup
    d=e.run(RunRequest(scenario_id='cloud_summary'))
    assert d.action=='redact' and d.delivered
    assert '010-1234-5678' not in r.received[0] and '900101-1234567' not in r.received[0]
    assert 'test.customer@example.test' not in str(s.events())

def test_approval_digest_single_use_and_revision(setup):
    s,j,r,e=setup;j.choice='insufficient'
    req=RunRequest(scenario_id='agent_mail')
    d=e.run(req);assert d.action=='review' and not r.received
    s.approve(d.approval_id,True)
    tamper=e.run(req.model_copy(update={'text':'different content','approval_id':d.approval_id}))
    assert tamper.action=='block' and not r.received
    approved=e.run(req.model_copy(update={'approval_id':d.approval_id}))
    assert approved.delivered and len(r.received)==1
    again=e.run(req.model_copy(update={'approval_id':d.approval_id}))
    assert again.action=='block' and len(r.received)==1

def test_unsupported_file_is_not_safe(setup):
    s,j,r,e=setup
    d=e.run(RunRequest(scenario_id='customer_upload',filename='file.zip',file_base64=base64.b64encode(b'abc').decode()))
    assert d.action=='block' and not d.extraction['complete'] and not j.calls and not r.received

def test_model_error_never_delivers(setup):
    s,j,r,e=setup
    def fail(*args):raise RuntimeError('private content must not leak')
    j.evaluate=fail
    d=e.run(RunRequest(scenario_id='agent_mail'))
    assert d.action=='block' and d.reason=='judge_unavailable' and not r.received
    assert 'private content' not in str(s.events())

def test_policy_conflict(setup):
    s,j,r,e=setup
    current=s.current();policies=[Policy.model_validate(p) for p in current['policies']]
    assert s.publish(1,policies)['revision']==2
    with pytest.raises(Conflict):s.publish(1,policies)
