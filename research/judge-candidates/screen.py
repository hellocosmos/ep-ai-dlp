"""Offline model screening against frozen synthetic development examples."""
import argparse, json, time
from pathlib import Path
p=argparse.ArgumentParser(); p.add_argument('model',choices=['laya','gliner']);p.add_argument('--limit',type=int,default=356);a=p.parse_args()
rows=[json.loads(x) for x in Path('judge/training/data/v1/dev.jsonl').read_text().splitlines()][:a.limit]
if a.model=='laya':
 from laya_mlx import Agent
 model=Agent('.local/judge-candidates/laya-multilingual-typed-decisions')
 def infer(r):
  out=model.predict(r['text'],{'decision':{'type':'choice','instructions':r['question'],'criteria':r['choices']}})
  ans=out['answers']['decision']
  return ans['choice'],out
else:
 import torch
 from gliner2 import AutoExtractor
 model=AutoExtractor.from_pretrained('.local/judge-candidates/GLiNER2.5-multi-Decide').to('mps').eval()
 def infer(r):
  with torch.inference_mode():
   out=model.classify_text(r['text'],{'decision':{'labels':r['choices'],'prompt':r['question']}},include_confidence=True)
  v=out['decision'];pred=v if isinstance(v,str) else v.get('label',v.get('value'))
  return pred,out
print('loaded',a.model,flush=True)
infer(rows[0])
path=Path('evidence/judge/candidates')/f'{a.model}-dev-{a.limit}.jsonl'
with path.open('w') as f:
 for i,r in enumerate(rows):
  start=time.perf_counter();pred,out=infer(r);elapsed=time.perf_counter()-start
  record={k:r[k] for k in ['id','source_family','policy_id','language','policy_language','gold','tags']}
  record.update(prediction=pred,latency_ms=elapsed*1000,raw=out)
  f.write(json.dumps(record,ensure_ascii=False)+'\n');f.flush()
  if i==0 or (i+1)%50==0: print(json.dumps({'n':i+1,'prediction':pred,'gold':r['gold'],'output':out if i==0 else None}),flush=True)
print('done',str(path),flush=True)
