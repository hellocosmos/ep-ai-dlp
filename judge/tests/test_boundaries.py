import base64
import io
import threading
import time
from pathlib import Path
import pytest
from openpyxl import Workbook
from aidlp_judge.documents import extract,extract_bounded,ExtractionError
from aidlp_judge.schemas import Policy,RunRequest
from aidlp_judge.worker import JudgeProcess
from aidlp_judge.backends import JudgeError
from test_enforcement import setup

def encoded(data):return base64.b64encode(data).decode()

def test_spreadsheet_formula_is_not_silently_skipped():
    wb=Workbook();wb.active['A1']='=HYPERLINK("https://example.test","private")';data=io.BytesIO();wb.save(data)
    with pytest.raises(ExtractionError,match='spreadsheet_formula_unsupported'):extract('a.xlsx',encoded(data.getvalue()))

def test_parser_deadline_terminates_worker():
    with pytest.raises(ExtractionError,match='document_extraction_timeout'):extract_bounded('a.txt',encoded(b'hello'),timeout=0)

def test_judge_deadline_terminates_worker():
    worker=JudgeProcess(timeout=0)
    try:
        with pytest.raises(JudgeError,match='judge_timeout'):worker.evaluate('decider-4b','test','test question',{'match':'yes','no_match':'no'})
        assert worker.proc is None and worker.pipe is None
    finally:worker.close()

def test_busy_rejects_without_starting_late_job():
    worker=JudgeProcess()
    worker.lock.acquire()
    try:
        with pytest.raises(JudgeError,match='judge_busy'):worker.evaluate('decider-4b','x','q',{'a':'a','b':'b'})
        assert worker.proc is None
    finally:worker.lock.release()

def test_acl_stops_file_extraction(setup,monkeypatch):
    s,j,r,e=setup
    def forbidden(*args):raise AssertionError('extractor should never be called')
    monkeypatch.setattr('aidlp_judge.engine.extract',forbidden)
    d=e.run(RunRequest(scenario_id='rag_acl',filename='x.xlsx',file_base64=encoded(b'not a file')))
    assert d.reason=='source_acl_denied_before_retrieval' and d.extraction['skipped_by_authorization']

def test_policy_change_during_judge_blocks(setup):
    s,j,r,e=setup
    original=j.evaluate
    def racing(*args):
        current=s.current();s.publish(current['revision'],[Policy.model_validate(p) for p in current['policies']]);return original(*args)
    j.evaluate=racing
    d=e.run(RunRequest(scenario_id='agent_mail',variant='allowed'))
    assert d.reason=='policy_changed_during_inspection' and not r.received

def test_approval_expires_and_revision_does_not_reuse(setup):
    s,j,r,e=setup;j.choice='insufficient';req=RunRequest(scenario_id='agent_mail')
    d=e.run(req);s.approve(d.approval_id,True)
    with s.transaction() as db:db.execute('UPDATE approvals SET expires=0 WHERE id=?',(d.approval_id,))
    assert not e.run(req.model_copy(update={'approval_id':d.approval_id})).delivered
    d=e.run(req);s.approve(d.approval_id,True);p=s.current();s.publish(p['revision'],[Policy.model_validate(v) for v in p['policies']])
    assert not e.run(req.model_copy(update={'approval_id':d.approval_id})).delivered

def test_audit_failure_prevents_send(setup):
    s,j,r,e=setup
    def fail(*args):raise RuntimeError('disk_unavailable')
    s.audit=fail
    with pytest.raises(RuntimeError):e.run(RunRequest(scenario_id='agent_mail',variant='allowed'))
    assert not r.received

def test_approval_cannot_override_hard_block(setup):
    s,j,r,e=setup;j.choice='insufficient';d=e.run(RunRequest(scenario_id='agent_mail'));s.approve(d.approval_id,True)
    blocked=e.run(RunRequest(scenario_id='m365_grounding',approval_id=d.approval_id))
    assert blocked.action=='block' and not r.received
