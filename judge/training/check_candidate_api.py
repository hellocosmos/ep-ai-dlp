"""Actual candidate HTTP contract + composed DLP checks, isolated state and receiver."""
import hashlib,json,sys,tempfile,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'judge'))
from aidlp_judge.engine import Engine
from aidlp_judge.store import Store
from aidlp_judge.schemas import RunRequest,Policy
from aidlp_judge.jev_client import JevClient
from aidlp_judge.delivery import HttpDelivery
from aidlp_judge.scenarios import SCENARIOS
from policies import POLICIES

class CandidateClient(JevClient):
    def evaluate(self,model,text,question,choices):
        return super().evaluate('decider-4b-dlp-v1',text,question,choices)

def main():
    origin='http://127.0.0.1:8312';token=(ROOT/'.local/jev-state/api.token').read_text().strip();checks={};rows=[];received=[]
    with httpx.Client(trust_env=False,timeout=40) as c:
        checks['health']=c.get(origin+'/health').json()['contract']=='aidlp-decision-v1'
        checks['auth_required']=c.get(origin+'/v1/models').status_code==401
        checks['decision_auth_required']=c.post(origin+'/v1/decide',json={}).status_code==401
        headers={'Authorization':'Bearer '+token}
        checks['candidate_listed']=any(m['id']=='decider-4b-dlp-v1' for m in c.get(origin+'/v1/models',headers=headers).json()['models'])
        question,*choices=POLICIES['customer_records']['en']
        request={'model_id':'decider-4b-dlp-v1','text':'Customer Rina paid $73 for a storage appliance; her latest refund remains pending.','question':question,'choices':dict(zip(['match','no_match','insufficient'],choices))}
        result=c.post(origin+'/v1/decide',headers=headers,json=request)
        checks['candidate_real_inference']=result.is_success and result.json().get('choice')=='match'
        if not result.is_success:raise RuntimeError(f'candidate warmup failed: {result.status_code} {result.text}')
        checks['adapter_revision']=len(result.json()['model_revision'])==64
        checks['no_generated_text']='generated_text' not in result.json()
        long_result=c.post(origin+'/v1/decide',headers=headers,json=request|{'text':'ordinary text '*3000})
        checks['overlength_fails_closed']=long_result.status_code==503 and long_result.json().get('reason')=='input_token_limit'
    class Sink(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));received.append(data)
            body=json.dumps({'id':data['id'],'content_hash':hashlib.sha256(data['text'].encode()).hexdigest()}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Sink);threading.Thread(target=server.serve_forever,daemon=True).start()
    client=CandidateClient(origin,token)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'state.sqlite');engine=Engine(store,client,HttpDelivery(f'http://127.0.0.1:{server.server_port}/receive','candidate-test'))
            for scenario in SCENARIOS:
                for variant in ['protected','allowed']:
                    before=len(received);request=RunRequest(scenario_id=scenario.id,variant=variant);d=engine.run(request);sent=len(received)-before
                    passed=True
                    if variant=='protected' and scenario.id!='cloud_summary':passed=d.action in {'block','review'} and sent==0 and d.reason not in {'judge_unavailable','judge_timeout','jev_api_unavailable'}
                    elif variant=='protected':passed=d.action=='redact' and sent==1 and all(x not in received[-1]['text'] for x in ['010-1234-5678','test.customer@example.test','900101-1234567'])
                    elif d.action=='review':
                        passed=sent==0 and bool(d.approval_id)
                        if passed:
                            store.approve(d.approval_id,True);approved=engine.run(request.model_copy(update={'approval_id':d.approval_id}));replay=engine.run(request.model_copy(update={'approval_id':d.approval_id}))
                            passed=approved.delivered and not replay.delivered and len(received)-before==1
                    else:passed=d.delivered and sent==1
                    if scenario.id in {'m365_grounding','rag_acl'} and variant=='protected':passed=passed and not d.findings and bool(d.extraction.get('skipped_by_authorization'))
                    rows.append({'scenario':scenario.id,'variant':variant,'action':d.action,'reason':d.reason,'passed':passed,'findings':len(d.findings)})
            checks['composed_scenarios']=all(r['passed'] for r in rows)
            # Force the review path using only this temporary store; no deployed policy changes.
            snapshot=store.current()
            test_policies=[Policy.model_validate(p).model_copy(update={'threshold':0.999}) for p in snapshot['policies']]
            store.publish(snapshot['revision'],test_policies)
            before=len(received);request=RunRequest(scenario_id='customer_upload',variant='allowed')
            reviewed=engine.run(request)
            checks['forced_review_before_send']=reviewed.action=='review' and len(received)==before and bool(reviewed.approval_id)
            checks['bound_approval_and_replay']=False
            if checks['forced_review_before_send']:
                store.approve(reviewed.approval_id,True)
                with_approval=request.model_copy(update={'approval_id':reviewed.approval_id})
                approved=engine.run(with_approval);replayed=engine.run(with_approval)
                checks['bound_approval_and_replay']=approved.delivered and not replayed.delivered and len(received)==before+1

            audit=json.dumps(store.events(),ensure_ascii=False);checks['audit_no_raw_content']='test.customer@example.test' not in audit and '김가람' not in audit
    finally:client.close();server.shutdown();server.server_close()
    output={'checks':checks,'candidate_probe':result.json(),'scenarios':rows,'scope':'isolated local HTTP receiver, incumbent policies, candidate model override in test client only','live_provider_connected':False,'production_default_changed':False}
    (ROOT/'evidence/judge/finetune/candidate-api-checks.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(output,ensure_ascii=False,indent=2));assert all(checks.values())
if __name__=='__main__':main()
