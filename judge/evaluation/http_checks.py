"""Check running authenticated loopback services without printing credentials."""
import json
import shlex
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[2]
def main():
    env={}
    for line in (ROOT/'.local/managed.env').read_text().splitlines():
        if '=' in line:
            k,v=line.split('=',1);parts=shlex.split(v);env[k]=parts[0] if parts else ''
    results={}
    token=(ROOT/'.local/judge-state/admin.token').read_text().strip()
    with httpx.Client(trust_env=False,timeout=10) as c:
        root='http://127.0.0.1:8310'
        results['direct_no_auth']=c.get(root+'/v1/status').status_code==401
        results['host_restricted']=c.get(root+'/health',headers={'host':'evil.example'}).status_code==400
        r=c.post(root+'/v1/run',headers={'authorization':'Bearer '+token},json={'scenario_id':'invalid','text':'PRIVATE-VALIDATION-CANARY'})
        results['validation_scrubs_content']=r.status_code==422 and 'PRIVATE-VALIDATION-CANARY' not in r.text
        results['sink_requires_separate_auth']=c.post(root+'/internal/sink',headers={'authorization':'Bearer '+token},json={}).status_code==401
        console='http://127.0.0.1:3100'
        results['proxy_no_auth']=c.get(console+'/api/judge/status').status_code==401
        login=c.post(console+'/api/auth/login',headers={'origin':console},json={'username':env['AIDLP_ADMIN_USER'],'password':env['AIDLP_ADMIN_PASSWORD']})
        results['normal_admin_login']=login.is_success
        r=c.get(console+'/api/judge/status')
        results['authenticated_status']=r.is_success and r.json().get('live_provider_connected') is False
        results['secret_not_in_response']=token not in r.text
        results['csrf_rejected']=c.post(console+'/api/judge/run',headers={'origin':'http://evil.example'},json={'scenario_id':'agent_mail'}).status_code==403
        results['sink_not_exposed_through_proxy']=c.post(console+'/api/judge/internal/sink',headers={'origin':console},json={}).status_code==404
        results['body_limit']=c.post(console+'/api/judge/run',headers={'origin':console,'content-type':'application/json'},content=b'x'*(6*1024*1024+1)).status_code==413
        c.post(console+'/api/auth/logout',headers={'origin':console},json={})
    (ROOT/'evidence/judge/http-checks.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2));assert all(results.values())
if __name__=='__main__':main()
