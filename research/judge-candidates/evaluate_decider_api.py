"""Score the incumbent GGUF/Q4 baseline with identical frozen DLP requests."""
import argparse,hashlib,json,math,time
from pathlib import Path
import httpx
p=argparse.ArgumentParser();p.add_argument('--model',default='decider-4b');p.add_argument('--port',type=int,default=8311);p.add_argument('--output-prefix',default='decider-4b-GGUF-Q4');a=p.parse_args()
root=Path(__file__).resolve().parents[2];out=root/'evidence/judge/winnow'
key=(root/'.local/jev-state/api.token').read_text().strip()
manifest=json.loads((root/'judge/training/data/v1/manifest.json').read_text())
with httpx.Client(base_url=f'http://127.0.0.1:{a.port}',headers={'Authorization':'Bearer '+key},timeout=35,trust_env=False) as client:
 for split in ['calibration','test']:
  p=root/'judge/training/data/v1'/f'{split}.jsonl';assert hashlib.sha256(p.read_bytes()).hexdigest()==manifest['splits'][split]['sha256']
  rows=[json.loads(s) for s in p.read_text().splitlines()];dest=out/f'{a.output_prefix}-{split}.jsonl'
  if dest.exists():raise RuntimeError('Refusing to overwrite '+str(dest))
  started=time.perf_counter()
  with dest.open('w') as f:
   for i,r in enumerate(rows):
    t=time.perf_counter();response=client.post('/v1/decide',json={'model_id':a.model,'text':r['text'],'question':r['question'],'choices':r['choices']});response.raise_for_status();d=response.json();ms=(time.perf_counter()-t)*1000
    scores=d['scores'];pred=d['choice']
    assert set(scores)==set(r['choices']) and pred==max(scores,key=scores.get)
    assert all(math.isfinite(x) and 0<=x<=1 for x in scores.values()) and math.isclose(sum(scores.values()),1,abs_tol=1e-5)
    record={k:r[k] for k in ['id','source_family','policy_id','language','policy_language','gold','tags']}
    record.update(prediction=pred,probabilities=scores,latency_ms=ms,input_tokens=d['input_tokens'],cached=d['cached'],runtime=d['runtime'],model_revision=d['model_revision'])
    f.write(json.dumps(record,ensure_ascii=False)+'\n');f.flush()
    if (i+1)%100==0:print(json.dumps({'split':split,'done':i+1,'total':len(rows),'seconds':round(time.perf_counter()-started,1)}),flush=True)
  print(json.dumps({'split':split,'complete':len(rows),'seconds':round(time.perf_counter()-started,1)}),flush=True)
