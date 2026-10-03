"""Standalone Jev-like decision service. No policy store, approval or delivery code.

Run behind TLS on a GPU server; authorization is mandatory on all model routes.
This is our bounded decision contract, not an official Jev API implementation.
"""
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI,Header,Depends,HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,model_validator
from .worker import JudgeProcess
from .errors import JudgeError

ROOT=Path(__file__).resolve().parents[2]
TOKEN_FILE=Path(os.environ.get('AIDLP_JEV_TOKEN_FILE',str(ROOT/'.local/jev-state/api.token')))
TOKEN_FILE.parent.mkdir(parents=True,exist_ok=True)
if 'AIDLP_JEV_TOKEN_FILE' not in os.environ:TOKEN_FILE.parent.chmod(0o700)
try:
    fd=os.open(TOKEN_FILE,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(36))
except FileExistsError:pass
TOKEN_FILE.chmod(0o600)
TOKEN=TOKEN_FILE.read_text().strip()
if len(TOKEN)<32:raise RuntimeError('invalid_jev_token')
worker=JudgeProcess(timeout=30)

@asynccontextmanager
async def lifespan(app):
    yield
    worker.close()
app=FastAPI(title='AI DLP Jev-like Decision API',version='1.0.0',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)

class LimitBody:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        chunks=[];size=0
        while True:
            value=await receive()
            if value['type']=='http.disconnect':return
            size+=len(value.get('body',b''))
            if size>512*1024:return await JSONResponse({'reason':'request_size_limit'},status_code=413)(scope,receive,send)
            chunks.append(value)
            if not value.get('more_body'):break
        async def replay():return chunks.pop(0) if chunks else await receive()
        return await self.app(scope,replay,send)
app.add_middleware(LimitBody)

@app.middleware('http')
async def no_store(request,call_next):
    response=await call_next(request);response.headers['Cache-Control']='no-store';return response

def auth(authorization:str|None=Header(default=None)):
    if not authorization or not secrets.compare_digest(authorization,'Bearer '+TOKEN):raise HTTPException(401,'authentication_required')

class DecideRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    model_id:Literal['decider-2b','decider-4b','standardone-3b','decider-4b-dlp-v1']
    text:str=Field(max_length=60000)
    question:str=Field(min_length=10,max_length=1500)
    choices:dict[str,str]
    @model_validator(mode='after')
    def valid_choices(self):
        if set(self.choices)!={'match','no_match','insufficient'} or any(not isinstance(v,str) or not 1<=len(v)<=700 for v in self.choices.values()):raise ValueError('invalid_choices')
        return self

@app.exception_handler(RequestValidationError)
async def validation_error(request,exc):return JSONResponse({'reason':'invalid_request'},status_code=422)
@app.exception_handler(Exception)
async def error(request,exc):return JSONResponse({'reason':'jev_service_error'},status_code=500)

@app.get('/health')
def health():return {'ok':True,'service':'aidlp-jev-decision-api','contract':'aidlp-decision-v1'}
@app.get('/v1/models',dependencies=[Depends(auth)])
def models():
    root=Path(os.environ.get('AIDLP_JEV_MODEL_ROOT',str(ROOT/'.local/judge-models')))
    result=[]
    for m in json.loads((ROOT/'research/judge-models/manifest.json').read_text()):
        p=root/m['id']/m['filename'];result.append({k:v for k,v in m.items() if k!='path'}|{'downloaded':p.exists(),'weight_bytes':p.stat().st_size if p.exists() else 0})
    if os.environ.get('AIDLP_ENABLE_MLX_CANDIDATE')=='1':
        from .mlx_backend import candidate_metadata
        result.append(candidate_metadata())
    return {'models':result,'worker_running':bool(worker.proc and worker.proc.is_alive()),'contract':'aidlp-decision-v1'}
@app.post('/v1/decide',dependencies=[Depends(auth)])
def decide(value:DecideRequest):
    try:return worker.evaluate(value.model_id,value.text,value.question,value.choices)
    except JudgeError as exc:return JSONResponse({'reason':str(exc)},status_code=503)
