"""Resumable public HTTP range download with final upstream SHA-256 verification."""
import concurrent.futures,hashlib,json,os,time,urllib.request
from pathlib import Path
meta=json.loads(Path('research/judge-candidates/Winnow-E4B-metadata.json').read_text())
item=next(x for x in meta['files'] if x['rfilename'].endswith('-BF16.gguf'))
dest=Path('.local/judge-candidates/Winnow-E4B')/item['rfilename'];dest.parent.mkdir(parents=True,exist_ok=True)
parts=dest.parent/'.bf16-parts';parts.mkdir(exist_ok=True)
size=item['size'];chunk=32*1024*1024;count=(size+chunk-1)//chunk
base='https://huggingface.co/'+meta['id']+'/resolve/'+meta['sha']+'/'+item['rfilename']
def download(i):
 start=i*chunk;end=min(size,start+chunk)-1;p=parts/f'{i:05d}.part'
 if p.exists() and p.stat().st_size==end-start+1:return p
 temp=p.with_suffix('.partial')
 for attempt in range(3):
  try:
   req=urllib.request.Request(base+'?download=true&segment='+str(i),headers={'Range':f'bytes={start}-{end}'})
   with urllib.request.urlopen(req,timeout=60) as r:
    if r.status!=206 or r.headers.get('Content-Range')!=f'bytes {start}-{end}/{size}':raise ValueError('Server did not honor exact byte range')
    with temp.open('wb') as f:
     remaining=end-start+1
     while remaining:
      b=r.read(min(1024*1024,remaining))
      if not b:raise IOError('Truncated range')
      f.write(b);remaining-=len(b)
   temp.replace(p);return p
  except Exception as e:
   if attempt==2:raise RuntimeError(f'Segment {i} failed: {type(e).__name__}') from None
   time.sleep(2*(attempt+1))
start=time.monotonic();done=0
with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
 for f in concurrent.futures.as_completed([pool.submit(download,i) for i in range(count)]):
  f.result();done+=1
  if done%8==0 or done==count:print(json.dumps({'segments':done,'total':count,'percent':round(done/count*100,1),'seconds':round(time.monotonic()-start,1)}),flush=True)
h=hashlib.sha256();temp=dest.with_suffix('.assembling')
with temp.open('wb') as out:
 for i in range(count):
  with (parts/f'{i:05d}.part').open('rb') as f:
   for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b);out.write(b)
if temp.stat().st_size!=size or h.hexdigest()!=item['lfs']['sha256']:raise RuntimeError('SHA256 mismatch')
temp.replace(dest)
print(json.dumps({'verified':True,'path':str(dest),'sha256':h.hexdigest(),'seconds':round(time.monotonic()-start,1)}),flush=True)
