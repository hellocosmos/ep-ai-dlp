"""Offline candidate inference. Prints probabilities; never performs enforcement."""
import argparse,json,time
from pathlib import Path
import mlx.core as mx
from mlx_decider import load_base,attach_saved_adapter,encode,logits,option_weights
from policies import POLICIES
from train import OUT,LABELS

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--text-file',type=Path,required=True)
    parser.add_argument('--policy',choices=sorted(POLICIES),required=True)
    parser.add_argument('--policy-language',choices=['ko','en'],default='ko')
    parser.add_argument('--base',action='store_true')
    parser.add_argument('--routing-profile',type=Path,
                        help='Optional offline action recommendation; never enforces or transmits')
    args=parser.parse_args()
    if args.text_file.stat().st_size>128*1024:parser.error('input_size_limit')
    profile=None
    if args.routing_profile:
        if args.base:parser.error('routing profile requires the fine-tuned candidate')
        from refine_calibration import load_profile
        profile=load_profile(args.routing_profile,OUT,Path(__file__).with_name('policies.py'))
    model,tok,_=load_base()
    if not args.base:attach_saved_adapter(model,OUT)
    model.eval();weights=option_weights(model,tok);mx.eval(model.parameters(),weights)
    question,*options=POLICIES[args.policy][args.policy_language]
    ids=encode(tok,args.text_file.read_text(),question,options)
    if len(ids)>768:parser.error('candidate_context_limit: no truncation or safe decision issued')
    start=time.perf_counter();probs=mx.softmax(logits(model,mx.array([ids]),mx.array([len(ids)]),weights),axis=-1);mx.eval(probs)
    scores=dict(zip(LABELS,probs.tolist()[0]));label=max(scores,key=scores.get)
    result={'model':'decider-4b-mlx-q4-base' if args.base else 'decider-4b-dlp-v1','policy':args.policy,'probabilities':scores,'label':label,'tokens':len(ids),'inference_seconds':time.perf_counter()-start,'enforcement_performed':False}
    if profile:
        from calibration_metrics import decision
        result.update(recommended_action=decision({'probabilities':scores},profile['thresholds']),
                      routing_thresholds=profile['thresholds'],routing_status=profile['status'],
                      model_revision=profile['model_revision'])
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
