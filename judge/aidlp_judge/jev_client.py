"""Authenticated remote decision API; this client never loads model weights."""
import math
import os
import ssl
from urllib.parse import urlsplit
import httpx
from .errors import JudgeError

class JevClient:
    def __init__(self,url,token,timeout=35,transport=None):
        parsed=urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}:
            raise ValueError('invalid_jev_origin')
        if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in {'127.0.0.1','localhost','::1'}):
            raise ValueError('jev_requires_https_or_loopback')
        if len(token)<32:raise ValueError('invalid_jev_token')
        self.origin=url.rstrip('/');self.timeout=timeout
        ca_file=os.environ.get('AIDLP_JEV_CA_FILE')
        verify=ssl.create_default_context(cafile=ca_file) if ca_file else True
        self.client=httpx.Client(verify=verify,headers={'Authorization':'Bearer '+token},trust_env=False,follow_redirects=False,transport=transport)

    def evaluate(self,model,text,question,choices):
        try:
            response=self.client.post(self.origin+'/v1/decide',json={'model_id':model,'text':text,'question':question,'choices':choices},timeout=self.timeout)
            if response.status_code==503:
                code=response.json().get('reason')
                if code in {'judge_busy','judge_timeout','input_token_limit','model_load_failed','gpu_offload_unavailable'}:raise JudgeError(code)
            if not response.is_success:raise JudgeError('jev_api_unavailable')
            value=response.json()
            if not isinstance(value,dict) or value.get('model_id')!=model:raise JudgeError('jev_invalid_response')
            scores=value.get('scores',{})
            if set(scores)!=set(choices) or any(not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in scores.values()):raise JudgeError('jev_invalid_response')
            if abs(sum(scores.values())-1)>0.001 or value.get('choice')!=max(scores,key=scores.get):raise JudgeError('jev_invalid_response')
            return value
        except httpx.TimeoutException:raise JudgeError('jev_api_timeout') from None
        except (httpx.HTTPError,ValueError,TypeError,AttributeError):raise JudgeError('jev_api_unavailable') from None

    def status(self):
        try:
            response=self.client.get(self.origin+'/v1/models',timeout=3)
            response.raise_for_status();value=response.json()
            if not isinstance(value.get('models'),list):raise ValueError()
            return {'connected':True,'models':value['models'],'worker_running':bool(value.get('worker_running'))}
        except (httpx.HTTPError,ValueError,TypeError,AttributeError):
            return {'connected':False,'models':[],'worker_running':False}

    def close(self):self.client.close()
