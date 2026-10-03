"""Loopback authenticated API for the local Judge lab and receiving adapter."""
import hashlib
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI,Depends,HTTPException,Header
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import Field
from .schemas import StrictModel,RunRequest,PolicyPublish,SCENARIO_IDS
from .store import Store,Conflict
from .jev_client import JevClient
from .engine import Engine
from .delivery import HttpDelivery
from .scenarios import list_scenarios

ROOT=Path(__file__).resolve().parents[2]
STATE=ROOT/'.local/judge-state'
STATE.mkdir(parents=True,exist_ok=True);STATE.chmod(0o700)

def get_secret(name):
    path=STATE/name
    try:
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(36))
    except FileExistsError:pass
    path.chmod(0o600)
    token=path.read_text().strip()
    if len(token)<32:raise RuntimeError('invalid_local_secret')
    return token

ADMIN_TOKEN=get_secret('admin.token')
SINK_TOKEN=get_secret('sink.token')
store=Store(STATE/'judge.sqlite')
JEV_TOKEN_FILE=Path(os.environ.get('AIDLP_JEV_TOKEN_FILE',str(ROOT/'.local/jev-state/api.token')))
judge=JevClient(os.environ.get('AIDLP_JEV_URL','http://127.0.0.1:8311'),JEV_TOKEN_FILE.read_text().strip())
delivery=HttpDelivery(os.environ.get('AIDLP_JUDGE_SINK','http://127.0.0.1:8310/internal/sink'),SINK_TOKEN)
engine=Engine(store,judge,delivery)

@asynccontextmanager
async def lifespan(app):
    yield
    judge.close()

app=FastAPI(title='AI DLP Local Judge',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','[::1]','testserver'])

class BodyLimit:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        chunks=[];size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            size+=len(message.get('body',b''))
            if size>6*1024*1024:
                return await JSONResponse({'message':'request_size_limit'},status_code=413)(scope,receive,send)
            chunks.append(message)
            if not message.get('more_body'):break
        async def replay():return chunks.pop(0) if chunks else await receive()
        return await self.app(scope,replay,send)
app.add_middleware(BodyLimit)

@app.middleware('http')
async def no_store(request,call_next):
    response=await call_next(request)
    response.headers['Cache-Control']='no-store';response.headers['X-Content-Type-Options']='nosniff'
    return response

def check_token(value,expected):
    if not value or not secrets.compare_digest(value,'Bearer '+expected):raise HTTPException(401,'authentication_required')
def admin(authorization:str|None=Header(default=None)):check_token(authorization,ADMIN_TOKEN)
def sink(authorization:str|None=Header(default=None)):check_token(authorization,SINK_TOKEN)

@app.exception_handler(RequestValidationError)
async def validation_error(request,exc):
    return JSONResponse({'message':'invalid_request'},status_code=422)

@app.exception_handler(Conflict)
async def conflict(request,exc):return JSONResponse({'message':str(exc)},status_code=409)
@app.exception_handler(Exception)
async def error(request,exc):return JSONResponse({'message':'local_service_error'},status_code=500)

@app.get('/health')
def health():return {'ok':True,'service':'aidlp-local-judge','scope':'controlled_local_adapter'}

@app.get('/v1/status',dependencies=[Depends(admin)])
def status():
    inference=judge.status()
    return dict(scope='controlled_local_adapter',live_provider_connected=False,models=inference['models'],
                policy=store.current(),scenarios=list_scenarios(),raw_content_audit=False,
                model_worker_running=inference['worker_running'],jev_api_connected=inference['connected'],
                inference_boundary='authenticated_separate_api')

@app.get('/v1/policy',dependencies=[Depends(admin)])
def policy():return store.current()
@app.post('/v1/policy',dependencies=[Depends(admin)])
def publish(value:PolicyPublish):
    if any(s not in SCENARIO_IDS for p in value.policies for s in p.scenarios):raise HTTPException(422,'unknown_scenario')
    return store.publish(value.expected_revision,value.policies)
@app.post('/v1/run',dependencies=[Depends(admin)])
def run(value:RunRequest):return engine.run(value)
@app.get('/v1/events',dependencies=[Depends(admin)])
def events():return store.events()
@app.get('/v1/receipts',dependencies=[Depends(admin)])
def receipts():return store.deliveries()
@app.get('/v1/approvals',dependencies=[Depends(admin)])
def approvals():return store.approvals()
class Approval(StrictModel):
    id:str=Field(pattern=r'^[a-f0-9-]{36}$')
    approved:bool
@app.post('/v1/approvals',dependencies=[Depends(admin)])
def approve(value:Approval):
    store.approve(value.id,value.approved)
    return {'ok':True}
@app.get('/v1/benchmark',dependencies=[Depends(admin)])
def benchmark():
    p=ROOT/'evidence/judge/benchmark/summary.json'
    return json.loads(p.read_text()) if p.exists() else {'reports':[],'state':'not_run'}

class SinkRequest(StrictModel):
    id:str=Field(pattern=r'^[a-f0-9-]{36}$')
    request_digest:str=Field(pattern=r'^[0-9a-f]{64}$')
    scenario_id:str=Field(max_length=40)
    text:str=Field(max_length=60000)
@app.post('/internal/sink',dependencies=[Depends(sink)])
def receive(value:SinkRequest):
    raw=value.text.encode();h=hashlib.sha256(raw).hexdigest()
    store.record_delivery(value.id,value.request_digest,value.scenario_id,h,len(raw))
    # Metadata-only receipt; raw text is inspected in-memory and never persisted.
    return {'id':value.id,'content_hash':h,'bytes':len(raw)}
