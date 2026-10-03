import httpx
import pytest
from aidlp_judge.jev_client import JevClient
from aidlp_judge.errors import JudgeError

TOKEN='synthetic-test-token-'+'x'*32
CHOICES={'match':'protected','no_match':'safe','insufficient':'unknown'}
def client(handler):return JevClient('http://127.0.0.1:8311',TOKEN,transport=httpx.MockTransport(handler))
def good():return {'choice':'no_match','scores':{'match':.01,'no_match':.98,'insufficient':.01},'model_id':'decider-4b','model_revision':'test','runtime':'test','input_tokens':10,'latency_ms':1,'cached':False}

def test_separate_api_request_auth_and_contract():
    def handler(req):
        assert req.url.path=='/v1/decide' and req.headers['authorization']=='Bearer '+TOKEN
        import json
        data=json.loads(req.content);assert data['text']=='content' and data['choices']==CHOICES
        return httpx.Response(200,json=good())
    c=client(handler)
    try:assert c.evaluate('decider-4b','content','a long question',CHOICES)['choice']=='no_match'
    finally:c.close()

@pytest.mark.parametrize('url',['http://gpu.internal:8311','https://user:password@gpu.test','https://gpu.test?token=secret','https://gpu.test/arbitrary/path'])
def test_remote_plaintext_or_credential_url_rejected(url):
    with pytest.raises(ValueError):JevClient(url,TOKEN)

@pytest.mark.parametrize('status',[401,403,500,302])
def test_api_errors_and_redirects_never_allow(status):
    c=client(lambda req:httpx.Response(status,headers={'location':'https://attacker.test'},json={'choice':'no_match'}))
    try:
        with pytest.raises(JudgeError,match='jev_api_unavailable'):c.evaluate('decider-4b','content','question',CHOICES)
    finally:c.close()

def test_transport_timeout_fails_closed():
    def fail(req):raise httpx.ReadTimeout('sensitive content must not echo')
    c=client(fail)
    try:
        with pytest.raises(JudgeError,match='^jev_api_timeout$'):c.evaluate('decider-4b','content','question',CHOICES)
    finally:c.close()

@pytest.mark.parametrize('patch',[{'model_id':'decider-2b'},{'choice':'match'},{'scores':{'match':.1,'no_match':.1,'insufficient':.1}}])
def test_malformed_or_wrong_model_response_rejected(patch):
    c=client(lambda req:httpx.Response(200,json=good()|patch))
    try:
        with pytest.raises(JudgeError,match='jev_invalid_response'):c.evaluate('decider-4b','content','question',CHOICES)
    finally:c.close()
