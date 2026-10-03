"""Identical frozen quality, length, concurrency and policy-count API probes."""
import argparse,concurrent.futures,json,math,statistics,time,uuid
import httpx
from small_judges import ROOT,NAMES,read_data
from policies import POLICIES

p=argparse.ArgumentParser();p.add_argument('model',choices=NAMES);p.add_argument('--adapted',action='store_true');p.add_argument('--port',type=int,default=8332);a=p.parse_args()
condition='adapted' if a.adapted else 'original';out=ROOT/'evidence/judge/small-comparison-bf16'/a.model
key=(ROOT/'.local/small-judge-eval.key').read_text().strip()
client=httpx.Client(base_url=f'http://127.0.0.1:{a.port}',headers={'Authorization':'Bearer '+key},timeout=40,trust_env=False)
def request(text,question,choices):
    began=time.perf_counter();response=client.post('/v1/decide',json={'text':text,'question':question,'choices':choices});ms=(time.perf_counter()-began)*1000
    return response.status_code,response.json(),ms
def validate(status,d):
    if status!=200:raise RuntimeError('Inference request failed: '+str(d))
    scores=d['scores']
    if set(scores)!= {'match','no_match','insufficient'} or d['choice']!=max(scores,key=scores.get) or any(not math.isfinite(v) or not 0<=v<=1 for v in scores.values()) or not math.isclose(sum(scores.values()),1,abs_tol=1e-4):raise RuntimeError('Invalid probability contract')
    assert d['output_tokens']==0 and not d['cached']

data=read_data();r=data['dev'][0];status,d,_=request(r['text'],r['question'],r['choices']);validate(status,d)
for split in ['calibration','test']:
    path=out/f'{condition}-{split}.jsonl'
    if path.exists():raise RuntimeError('Refusing to overwrite '+str(path))
    began=time.perf_counter()
    with path.open('w') as f:
        for i,r in enumerate(data[split],1):
            status,d,ms=request(r['text'],r['question'],r['choices']);validate(status,d)
            row={k:r[k] for k in ['id','source_family','policy_id','language','policy_language','gold','tags']}
            row.update(prediction=d['choice'],probabilities=d['scores'],latency_ms=ms,input_tokens=d['input_tokens'],output_tokens=d['output_tokens'],cached=d['cached'],gpu_active_bytes=d['gpu_active_bytes'],queue_ms=d['queue_ms'],inference_ms=d['inference_ms'])
            f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush()
            if i%100==0:print(json.dumps({'model':a.model,'condition':condition,'split':split,'done':i,'total':len(data[split]),'seconds':time.perf_counter()-began}),flush=True)
    print(json.dumps({'event':'quality_complete','split':split,'condition':condition}),flush=True)
nonce=uuid.uuid4().hex[:8];question=POLICIES['commercial_terms']['ko'][0];choices=dict(zip(['match','no_match','insufficient'],POLICIES['commercial_terms']['ko'][1:]))
def probe(group,i,repeats):
    text='공개 제품 안내입니다. 표준 기능은 문서 검색과 일정 관리입니다. '*repeats+f'\n테스트 문서 번호: {nonce}-{group}-{i}'
    status,d,ms=request(text,question,choices)
    return {'group':group,'id':i,'status':status,'ms':ms,'result':d}
probes=[]
for group,repeats in [('short',1),('medium',12),('long',48)]:
    for i in range(20):probes.append(probe(group,i,repeats))
for batch in range(5):
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:probes.extend(pool.map(lambda i:probe('concurrent4',i,1),range(batch*4,batch*4+4)))
multi=[]
for i in range(10):
    text='공개 제품 안내입니다. 표준 기능은 문서 검색과 일정 관리입니다. '*12+f'\n테스트 문서 번호: {nonce}-multi-{i}'
    began=time.perf_counter();statuses=[]
    for values in POLICIES.values():
        status,d,ms=request(text,values['ko'][0],dict(zip(['match','no_match','insufficient'],values['ko'][1:])));statuses.append(status)
    multi.append({'ms':(time.perf_counter()-began)*1000,'statuses':statuses})
summary={}
for group in ['short','medium','long','concurrent4']:
    rows=[r for r in probes if r['group']==group];lat=sorted(r['ms'] for r in rows);successful=sorted(r['ms'] for r in rows if r['status']==200)
    summary[group]={'n':len(rows),'success':len(successful),'p50_ms':statistics.median(lat),'p95_ms':lat[int((len(lat)-1)*.95)],'successful_p95_ms':successful[int((len(successful)-1)*.95)] if successful else None,'errors':[r['result'] for r in rows if r['status']!=200]}
summary['six_sequential']={'n':10,'success':sum(all(s==200 for s in r['statuses']) for r in multi),'p50_ms':statistics.median(r['ms'] for r in multi)}
(out/f'{condition}-latency.json').write_text(json.dumps(summary,indent=2)+'\n')
(out/f'{condition}-latency-raw.json').write_text(json.dumps({'requests':probes,'multi_policy':multi},ensure_ascii=False,indent=2)+'\n')
client.close();print(json.dumps({'event':'complete','summary':summary}),flush=True)
