import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from huggingface_hub import snapshot_download
ROOT=Path(__file__).resolve().parents[2]
def fetch(name):
 meta=json.loads((ROOT/'research/judge-candidates'/(name.replace('/','--')+'.json')).read_text())
 dest=ROOT/'.local/judge-candidates'/name.split('/')[-1]
 snapshot_download(name,revision=meta['sha'],local_dir=dest,allow_patterns=['*.json','*.safetensors','README.md','LICENSE','NOTICE'],max_workers=3)
 print(json.dumps({'model':name,'revision':meta['sha'],'path':str(dest)}),flush=True)
with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(fetch,['alfred361/laya-multilingual-typed-decisions','fastino/GLiNER2.5-multi-Decide']))
