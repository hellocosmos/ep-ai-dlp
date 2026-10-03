"""Bounded Winnow latency/load probe with synthetic Korean content."""
import argparse,concurrent.futures,json,statistics,time,uuid,sys
from pathlib import Path
import httpx
p=argparse.ArgumentParser();p.add_argument('quant',choices=['Q8_0','Q6_K','Q5_K_M','Q4_K_M']);a=p.parse_args()
root=Path(__file__).resolve().parents[2];key=(root/'.local/winnow-eval.key').read_text().strip()
client=httpx.Client(base_url='http://127.0.0.1:8331',headers={'Authorization':'Bearer '+key},timeout=90,trust_env=False)
nonce=uuid.uuid4().hex[:8]
question='이 자료에 특정 고객과 합의한 비공개 계약 가격 또는 내부 원가가 포함되어 있는가? 공개된 일반 가격표는 제외한다.'
choices={'match':'특정 거래 상대방의 비공개 조건 또는 내부 원가가 있다.','no_match':'일반적인 공개 안내만 있으며 비공개 거래 조건은 없다.','insufficient':'자료가 부족하거나 모호하다.'}
def request(group,i,repeats):
 text='공개 제품 안내입니다. 표준 기능은 문서 검색과 일정 관리입니다. '*repeats+f'\n테스트 문서 번호: {nonce}-{group}-{i}'
 body={'model':'Winnow-E4B','state':text,'questions':{'decision':{'type':'choice','instructions':question,'criteria':choices}},'winnow':{'temperature':1.0,'diagnostics':True,'reuse_prefix':False}}
 t=time.perf_counter();r=client.post('/v1/systemone',json=body);ms=(time.perf_counter()-t)*1000;d=r.json()
 return {'group':group,'id':i,'status':r.status_code,'roundtrip_ms':ms,'usage':d.get('usage'),'diagnostics':d.get('winnow'),'error':d.get('error')}
rows=[request('first_request',0,1)]
for group,repeats in [('short',1),('medium',12),('long',48)]:
 for i in range(20):rows.append(request(group,i,repeats))
for batch in range(5):
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:rows.extend(pool.map(lambda i:request('concurrent4',i,1),range(batch*4,batch*4+4)))
# Compare six policy calls with one shared-state request; not production integration.
sys.path.insert(0,str(root/'judge/training'))
from policies import POLICIES
multi=[]
for mode in ['six_sequential','six_shared_state']:
 for iteration in range(10):
  text='공개 제품 안내입니다. 표준 기능은 문서 검색과 일정 관리입니다. '*12+f'\n테스트 문서 번호: {nonce}-{mode}-{iteration}'
  questions={pid:{'type':'choice','instructions':values['ko'][0],'criteria':dict(zip(['match','no_match','insufficient'],values['ko'][1:]))} for pid,values in POLICIES.items()}
  groups=[questions] if mode=='six_shared_state' else [{k:v} for k,v in questions.items()]
  began=time.perf_counter();statuses=[];answers={}
  for group in groups:
   response=client.post('/v1/systemone',json={'model':'Winnow-E4B','state':text,'questions':group,'winnow':{'temperature':1.0,'diagnostics':True,'reuse_prefix':False}})
   statuses.append(response.status_code)
   if response.is_success:answers.update(response.json()['answers'])
  multi.append({'mode':mode,'iteration':iteration,'ms':(time.perf_counter()-began)*1000,'statuses':statuses,'answers':answers})
client.close();out=root/'evidence/judge/winnow';
(out/f'Winnow-E4B-{a.quant}-multi-policy.json').write_text(json.dumps(multi,ensure_ascii=False,indent=2)+'\n')
(out/f'Winnow-E4B-{a.quant}-api-latency.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
summary={}
for group in ['first_request','short','medium','long','concurrent4']:
 s=[r for r in rows if r['group']==group];lat=sorted(r['roundtrip_ms'] for r in s)
 summary[group]={'n':len(s),'success':sum(r['status']==200 for r in s),'p50_ms':statistics.median(lat),'p95_ms':lat[int((len(lat)-1)*.95)],'input_tokens':sorted({r['usage']['input_tokens'] for r in s if r['usage']}),'errors':[r['error'] for r in s if r['status']!=200]}
(out/f'Winnow-E4B-{a.quant}-api-latency.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
