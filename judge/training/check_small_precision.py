"""Development-only BF16 check so quantization loss is not mistaken for model quality."""
import argparse,json,time
from small_judges import ROOT,NAMES,SmallJudge,read_data
from train_small import prepare,evaluate,metrics,write_json,write_rows
p=argparse.ArgumentParser();p.add_argument('model',choices=NAMES);a=p.parse_args()
j=SmallJudge(a.model,bits=0);items=prepare(j,read_data()['dev']);began=time.perf_counter();rows=evaluate(j,items,4)
out=ROOT/'evidence/judge/small-comparison'/a.model
write_rows(out/'bf16-dev.jsonl',rows);result=metrics(rows)|{'seconds':time.perf_counter()-began,'precision':'publisher BF16 weights, MLX kernels, original trained readout'}
write_json(out/'bf16-dev-summary.json',result);print(json.dumps(result),flush=True)
