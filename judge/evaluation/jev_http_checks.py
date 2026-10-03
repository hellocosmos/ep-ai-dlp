"""Integration checks for the independently running decision API."""
import json
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[2]
def main():
    token=(ROOT/'.local/jev-state/api.token').read_text().strip()
    h={'Authorization':'Bearer '+token};url='http://127.0.0.1:8311'
    results={}
    with httpx.Client(trust_env=False,timeout=40) as c:
        results['public_health_contract']=c.get(url+'/health').json().get('contract')=='aidlp-decision-v1'
        results['models_require_auth']=c.get(url+'/v1/models').status_code==401
        results['decide_requires_auth']=c.post(url+'/v1/decide',json={}).status_code==401
        models=c.get(url+'/v1/models',headers=h).json()
        results['three_pinned_models']=len(models['models'])==3 and all(m['downloaded'] for m in models['models'])
        payload={'model_id':'decider-4b','text':'Public retail catalogue: all customers pay the same list price.','question':'Does this contain private, customer-specific negotiated deal terms?','choices':{'match':'Private negotiated terms are present.','no_match':'Only general public information is present.','insufficient':'Insufficient information.'}}
        r=c.post(url+'/v1/decide',headers=h,json=payload)
        results['real_model_typed_scores']=r.is_success and r.json().get('model_revision')=='b79f09d9ba7837f1b744295ea267b55d08e958ec' and set(r.json().get('scores',{}))==set(payload['choices'])
        result=r.json()
        results['no_generated_text']='generated_text' not in result and result.get('input_tokens',0)>0
        r=c.post(url+'/v1/decide',headers=h,json=payload|{'model_id':'invalid','text':'PRIVATE-VALIDATION-CANARY'})
        results['validation_does_not_echo_text']=r.status_code==422 and 'PRIVATE-VALIDATION-CANARY' not in r.text
        results['no_policy_route']=c.get(url+'/v1/policy',headers=h).status_code==404
        results['no_send_route']=c.post(url+'/internal/sink',headers=h,json={}).status_code==404
        results['size_limit']=c.post(url+'/v1/decide',headers=h|{'content-type':'application/json'},content=b'x'*(512*1024+1)).status_code==413
        results['no_store']=r.headers.get('cache-control')=='no-store'
    (ROOT/'evidence/judge/jev-api-checks.json').write_text(json.dumps({'checks':results,'real_model_response':result},indent=2)+'\n')
    print(json.dumps(results,indent=2));assert all(results.values())
if __name__=='__main__':main()
