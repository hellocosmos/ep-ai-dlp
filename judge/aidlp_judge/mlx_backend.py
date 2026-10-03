"""Opt-in MLX fine-tuned candidate, served through the isolated decision worker."""
import hashlib,json,math,os,sys,time
from pathlib import Path
from .errors import JudgeError
ROOT=Path(__file__).resolve().parents[2]
MODEL_ID='decider-4b-dlp-v1'

def candidate_metadata():
    folder=ROOT/'.local/finetune'/MODEL_ID
    path=folder/'adapters.safetensors'
    return {'id':MODEL_ID,'name':'Decider 4B AI DLP bilingual candidate','downloaded':path.exists(),'weight_bytes':path.stat().st_size if path.exists() else 0,'runtime':'MLX/Metal/4bit-group64/LoRA','experimental':True,'context_limit':768,'production_default':False}

class MLXJudge:
    def __init__(self,model_id):
        if model_id!=MODEL_ID or os.environ.get('AIDLP_ENABLE_MLX_CANDIDATE')!='1':raise JudgeError('model_not_enabled')
        sys.path.insert(0,str(ROOT/'judge/training'))
        import mlx.core as mx
        from mlx_decider import load_base,attach_saved_adapter,option_weights
        self.mx=mx;self.model_id=model_id
        folder=ROOT/'.local/finetune'/MODEL_ID
        if not (folder/'completion.json').exists():raise JudgeError('model_training_incomplete')
        weights=folder/'adapters.safetensors'
        digest=hashlib.sha256(weights.read_bytes()).hexdigest()
        artifact=json.loads((folder/'artifact-manifest.json').read_text())
        config_digest=hashlib.sha256((folder/'adapter_config.json').read_bytes()).hexdigest()
        if digest!=artifact['adapter_sha256'] or config_digest!=artifact['files']['adapter_config.json']:raise JudgeError('model_integrity_failed')
        revision=hashlib.sha256((digest+config_digest+artifact['base_revision']+'MLX/Metal/4bit-group64/LoRA').encode()).hexdigest()
        if revision!=artifact['model_revision']:raise JudgeError('model_integrity_failed')
        self.revision=revision;self.model,self.tok,_=load_base();attach_saved_adapter(self.model,folder)
        self.weights=option_weights(self.model,self.tok);mx.eval(self.model.parameters(),self.weights)
    def evaluate(self,text,question,choices,**kwargs):
        from mlx_decider import encode,logits
        if set(choices)!={'match','no_match','insufficient'}:raise JudgeError('invalid_choices')
        mx=self.mx;start=time.perf_counter();ids=encode(self.tok,text,question,list(choices.values()))
        if len(ids)>768:raise JudgeError('input_token_limit')
        probs=mx.softmax(logits(self.model,mx.array([ids]),mx.array([len(ids)]),self.weights),axis=-1);mx.eval(probs)
        scores=dict(zip(choices,probs.tolist()[0]))
        if any(not math.isfinite(v) for v in scores.values()):raise JudgeError('nonfinite_logits')
        return {'choice':max(scores,key=scores.get),'scores':scores,'model_id':self.model_id,'model_revision':self.revision,'runtime':'MLX/Metal/4bit-group64/LoRA','input_tokens':len(ids),'latency_ms':round((time.perf_counter()-start)*1000,3),'cached':False}
    def close(self):
        del self.model,self.weights;self.mx.clear_cache()
