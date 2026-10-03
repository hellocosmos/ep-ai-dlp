"""Bounded loopback API timing; synthetic content, no enforcement calls."""
import concurrent.futures,json,statistics,time,uuid
from pathlib import Path
import httpx
root=Path(__file__).resolve().parents[2]
key=(root/'.local/jev-state/api.token').read_text().strip()
client=httpx.Client(base_url='http://127.0.0.1:8311',headers={'Authorization':'Bearer '+key},timeout=35,trust_env=False)
nonce=uuid.uuid4().hex[:8]
question='이 자료에 특정 고객과 합의한 비공개 계약 가격 또는 내부 원가가 포함되어 있는가? 공개된 일반 가격표는 제외한다.'
choices={'match':'특정 거래 상대방의 비공개 조건 또는 내부 원가가 있다.','no_match':'일반적인 공개 안내만 있으며 비공개 거래 조건은 없다.','insufficient':'자료가 부족하거나 모호하다.'}
def request(group,i,repeats):
 text='공개 제품 안내입니다. 표준 기능은 문서 검색과 일정 관리입니다. '*repeats+f'\n테스트 문서 번호: {nonce}-{group}-{i}'
 t=time.perf_counter();r=client.post('/v1/decide',json={'model_id':'decider-4b','text':text,'question':question,'choices':choices});ms=(time.perf_counter()-t)*1000
 d=r.json();return {'group':group,'id':i,'status':r.status_code,'roundtrip_ms':ms,'input_tokens':d.get('input_tokens'),'inference_ms':d.get('latency_ms'),'cached':d.get('cached'),'reason':d.get('reason')}
rows=[request('first_request',0,1)]
for group,repeats in [('short',1),('medium',12),('long',48)]:
 for i in range(20):rows.append(request(group,i,repeats))
# Small bounded load probe; busy responses count as failures, never excluded.
for batch in range(5):
 with concurrent.futures.ThreadPoolExecutor(max_workers=4) as p:rows.extend(p.map(lambda i:request('concurrent4',i,1),range(batch*4,batch*4+4)))
client.close();out=root/'evidence/judge/winnow';(out/'decider-q4-api-latency.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
summary={}
for group in ['first_request','short','medium','long','concurrent4']:
 s=[r for r in rows if r['group']==group];lat=sorted(r['roundtrip_ms'] for r in s)
 summary[group]={'n':len(s),'success':sum(r['status']==200 for r in s),'cached':sum(r['cached'] is True for r in s),'p50_ms':statistics.median(lat),'p95_ms':lat[int((len(lat)-1)*.95)],'tokens':sorted({r['input_tokens'] for r in s if r['input_tokens'] is not None}),'errors':[r['reason'] for r in s if r['status']!=200]}
(out/'decider-q4-api-latency.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary,indent=2))
