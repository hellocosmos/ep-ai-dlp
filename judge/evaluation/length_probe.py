"""Supplemental length/latency probe; never used to tune held-out thresholds."""
import json
from pathlib import Path
from aidlp_judge.backends import LocalJudge,JudgeError
from aidlp_judge.defaults import default_policies
ROOT=Path(__file__).resolve().parents[2]
def main():
 j=LocalJudge('decider-4b');p=default_policies()[1];rows=[]
 try:
  for repeats in [1,50,150,350,700]:
   text=('공개 제품 설명: 안전한 계정 관리와 정보보호 교육을 제공합니다.\n'*repeats)+'한빛상사 계약: 내부원가 4200원, 공급가 8000원, 협상 할인 상한 12%.'
   try:r=j.evaluate(text,p.question,{'match':p.match_description,'no_match':p.no_match_description,'insufficient':'Insufficient information.'},use_cache=False)
   except JudgeError as e:r={'error':str(e)}
   rows.append({'characters':len(text),**r})
  (ROOT/'evidence/judge/length-probe.json').write_text(json.dumps(rows,indent=2)+'\n');print(json.dumps(rows,indent=2))
 finally:j.close()
if __name__=='__main__':main()
