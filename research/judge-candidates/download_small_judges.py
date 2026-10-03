"""Pinned, resumable public checkpoint downloads with full-file hash validation."""
import concurrent.futures,hashlib,json,time,urllib.request
from pathlib import Path
from huggingface_hub import HfApi

ROOT=Path(__file__).resolve().parents[2]
MODELS={'decider-2b':'Mapika/decider-2b','jeff-qwen-2b':'mstrasser/Jeff-Qwen3.5-2B','jeff-gemma-e2b':'mstrasser/Jeff-Gemma4-E2B'}
OUT=ROOT/'evidence/judge/small-comparison';OUT.mkdir(parents=True,exist_ok=True)
CHUNK=32*1024*1024
def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()
def fetch(repo,rev,item,directory):
    name=item.rfilename;dest=directory/name;dest.parent.mkdir(parents=True,exist_ok=True)
    size=item.size;expected=item.lfs.sha256 if item.lfs else None
    if dest.exists():
        if dest.stat().st_size==size and (expected is None or digest(dest)==expected):return
        raise RuntimeError('Existing checkpoint failed verification: '+str(dest))
    url=f'https://huggingface.co/{repo}/resolve/{rev}/{name}'
    if size<CHUNK:
        with urllib.request.urlopen(url,timeout=120) as r:payload=r.read()
        if len(payload)!=size:raise RuntimeError('Unexpected file length')
        if expected and hashlib.sha256(payload).hexdigest()!=expected:raise RuntimeError('SHA256 mismatch')
        if not expected and hashlib.sha1(f'blob {size}\0'.encode()+payload).hexdigest()!=item.blob_id:raise RuntimeError('Git blob mismatch')
        temp=dest.with_suffix(dest.suffix+'.partial');temp.write_bytes(payload);temp.replace(dest);return
    parts=directory/'.parts'/name;parts.mkdir(parents=True,exist_ok=True);count=(size+CHUNK-1)//CHUNK
    def segment(i):
        lo=i*CHUNK;hi=min(size,lo+CHUNK)-1;p=parts/f'{i:05d}.part'
        if p.exists() and p.stat().st_size==hi-lo+1:return
        for attempt in range(4):
            try:
                req=urllib.request.Request(url+'?download=true&segment='+str(i),headers={'Range':f'bytes={lo}-{hi}'})
                with urllib.request.urlopen(req,timeout=90) as r:
                    if r.status!=206 or r.headers.get('Content-Range')!=f'bytes {lo}-{hi}/{size}':raise RuntimeError('Invalid Content-Range')
                    payload=r.read()
                if len(payload)!=hi-lo+1:raise RuntimeError('Truncated segment')
                temp=p.with_suffix('.partial');temp.write_bytes(payload);temp.replace(p);return
            except Exception:
                if attempt==3:raise
                time.sleep(2*(attempt+1))
    began=time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        for done,f in enumerate(concurrent.futures.as_completed([pool.submit(segment,i) for i in range(count)]),1):
            f.result()
            if done%16==0 or done==count:print(json.dumps({'file':name,'model':repo,'chunks':done,'total':count,'seconds':round(time.monotonic()-began,1)}),flush=True)
    h=hashlib.sha256();temp=dest.with_suffix(dest.suffix+'.assembling')
    with temp.open('wb') as target:
        for i in range(count):
            with (parts/f'{i:05d}.part').open('rb') as f:
                for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b);target.write(b)
    if temp.stat().st_size!=size or h.hexdigest()!=expected:raise RuntimeError('SHA256 mismatch')
    temp.replace(dest)

api=HfApi()
for alias,repo in MODELS.items():
    pinned=json.loads((ROOT/'research/judge-candidates'/(repo.replace('/','--')+'-metadata.json')).read_text())['sha']
    info=api.model_info(repo,revision=pinned,files_metadata=True);directory=ROOT/'.local/finetune/small-judges'/alias;directory.mkdir(parents=True,exist_ok=True)
    files=[x for x in info.siblings if not x.rfilename.startswith('.') and (x.rfilename.endswith(('.json','.jinja','.safetensors','.md')) or x.rfilename in ['LICENSE','NOTICE','decider/prompt.py'])]
    files.sort(key=lambda f:f.size)
    manifest={'id':repo,'revision':info.sha,'files':[{'name':x.rfilename,'size':x.size,'sha256':x.lfs.sha256 if x.lfs else None,'git_blob':x.blob_id} for x in files]}
    (directory/'source-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for item in files:fetch(repo,info.sha,item,directory)
    (directory/'download-complete.json').write_text(json.dumps({'verified':True,'revision':info.sha})+'\n')
    print(json.dumps({'event':'model_verified','alias':alias,'path':str(directory)}),flush=True)
