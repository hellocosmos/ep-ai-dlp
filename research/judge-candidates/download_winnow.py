import json,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from huggingface_hub import snapshot_download

def fetch(model):
 m=json.loads(Path('research/judge-candidates/'+model+'-metadata.json').read_text())
 dest=Path('.local/judge-candidates')/model
 snapshot_download(m['id'],revision=m['sha'],local_dir=dest,allow_patterns=[x['rfilename'] for x in m['files']],max_workers=2)
 for f in m['files']:
  if 'lfs' not in f:continue
  p=dest/f['rfilename']; h=hashlib.sha256()
  with p.open('rb') as stream:
   for b in iter(lambda:stream.read(8*1024*1024),b''):h.update(b)
  assert p.stat().st_size==f['size'] and h.hexdigest()==f['lfs']['sha256']
 print(json.dumps({'model':model,'revision':m['sha'],'verified':True}),flush=True)
with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(fetch,['Winnow-E4B','Winnow-12B']))
