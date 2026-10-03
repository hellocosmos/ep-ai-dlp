"""Frozen synthetic DLP screening through a local Winnow server, batch one."""
import argparse,hashlib,json,math,time,urllib.request
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('model',choices=['Winnow-E4B','Winnow-12B']);p.add_argument('--port',type=int,default=8331);p.add_argument('--split',choices=['dev','calibration','test'],default='dev');p.add_argument('--limit',type=int);p.add_argument('--suffix',default='');p.add_argument('--quant',choices=['Q8_0','Q6_K','Q5_K_M','Q4_K_M'],default='Q8_0');a=p.parse_args()
root=Path(__file__).resolve().parents[2];data=root/'judge/training/data/v1'/f'{a.split}.jsonl'
manifest=json.loads((data.parent/'manifest.json').read_text());assert hashlib.sha256(data.read_bytes()).hexdigest()==manifest['splits'][a.split]['sha256']
rows=[json.loads(s) for s in data.read_text().splitlines()];rows=rows[:a.limit] if a.limit else rows
key=(root/'.local/winnow-eval.key').read_text().strip();temp=1.0  # Same uncalibrated temperature; fit action gates separately per quantization.
outpath=root/'evidence/judge/winnow'/f'{a.model}-{a.quant}-{a.split}{a.suffix}.jsonl'
if outpath.exists():raise SystemExit('Refusing to overwrite existing evaluation: '+str(outpath))
def infer(r):
 body={'model':a.model,'state':r['text'],'questions':{'decision':{'type':'choice','instructions':r['question'],'criteria':r['choices']}},'winnow':{'temperature':temp,'diagnostics':True,'reuse_prefix':False}}
 req=urllib.request.Request(f'http://127.0.0.1:{a.port}/v1/systemone',data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
 with urllib.request.urlopen(req,timeout=90) as f:out=json.load(f)
 ans=out['answers']['decision'];scores=ans['probabilities'];pred=ans['choice']
 if set(scores)!=set(r['choices']) or pred!=max(scores,key=scores.get):raise ValueError('Invalid choice or scores')
 if any(not math.isfinite(v) or not 0<=v<=1 for v in scores.values()) or not math.isclose(sum(scores.values()),1,abs_tol=1e-4):raise ValueError('Invalid probabilities')
 if out['usage'].get('output_tokens')!=0:raise ValueError('Unexpected generation')
 return out
warm=infer(rows[0]);print(json.dumps({'event':'warmup','model':a.model,'output':warm}),flush=True)
start=time.perf_counter()
with outpath.open('w') as f:
 for i,r in enumerate(rows):
  t=time.perf_counter();out=infer(r);ms=(time.perf_counter()-t)*1000;ans=out['answers']['decision']
  record={k:r[k] for k in ['id','source_family','policy_id','language','policy_language','gold','tags']}
  record.update(prediction=ans['choice'],probabilities=ans['probabilities'],latency_ms=ms,usage=out['usage'],diagnostics=out.get('winnow'),model=a.model,quantization=a.quant,temperature=temp)
  f.write(json.dumps(record,ensure_ascii=False)+'\n');f.flush()
  if (i+1)%50==0:print(json.dumps({'model':a.model,'split':a.split,'completed':i+1,'total':len(rows),'seconds':time.perf_counter()-start}),flush=True)
print(json.dumps({'event':'complete','rows':len(rows),'seconds':time.perf_counter()-start,'path':str(outpath)}),flush=True)
