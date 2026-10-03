"""Decode-free local decision inference using pinned GGUF checkpoints and Metal.

Prompt formats and temperature values follow each model publisher's contract.
No model-provided Python or generated text is executed. Only option logits are read.
"""
import ctypes
import hashlib
import json
import math
import os
import sys
import threading
import time
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
MODEL_ROOT=Path(os.environ.get('AIDLP_JEV_MODEL_ROOT',str(ROOT/'.local/judge-models')))
MANIFEST=ROOT/'research/judge-models/manifest.json'

from .errors import JudgeError

class LocalJudge:
    def __init__(self,model_id:str,n_ctx:int=8192):
        import llama_cpp as L
        self.L=L
        candidates=json.loads(MANIFEST.read_text())
        self.spec=next((m for m in candidates if m['id']==model_id),None)
        if self.spec is None:raise JudgeError('model_not_downloaded')
        self.folder=MODEL_ROOT/model_id
        self.model_id=model_id
        self.revision=self.spec['revision']
        backend='Metal' if sys.platform=='darwin' else 'GPU-offload'
        self.runtime=f'llama.cpp/llama-cpp-python-{L.__version__}/{backend}/Q4_K_M'
        self.lock=threading.Lock()
        self.n_ctx=n_ctx
        self.model=self.ctx=self.batch=None
        self.cache={}
        self.log_callback=L.llama_log_callback(lambda level,message,data:None)
        L.llama_log_set(self.log_callback,ctypes.c_void_p())
        L.llama_backend_init()
        if not L.llama_supports_gpu_offload():raise JudgeError('gpu_offload_unavailable')
        mp=L.llama_model_default_params();mp.n_gpu_layers=-1
        self.model=L.llama_model_load_from_file(os.fsencode(self.folder/self.spec['filename']),mp)
        if not self.model:raise JudgeError('model_load_failed')
        self.vocab=L.llama_model_get_vocab(self.model)
        self.n_vocab=L.llama_vocab_n_tokens(self.vocab)
        cp=L.llama_context_default_params()
        cp.n_ctx=n_ctx;cp.n_batch=1024;cp.n_ubatch=512;cp.n_seq_max=1
        cp.n_threads=8;cp.n_threads_batch=8
        self.ctx=L.llama_init_from_model(self.model,cp)
        if not self.ctx:
            self.close();raise JudgeError('context_initialization_failed')
        self.batch=L.llama_batch_init(1024,0,1)
        self.temperature=0.9
        if model_id.startswith('decider'):
            cfg=json.loads((self.folder/'decider_config.json').read_text())
            if cfg.get('layout','plain')!='plain':raise JudgeError('unsupported_prompt_layout')
            self.temperature=cfg.get('temperature_by_type',{}).get('choice',cfg['temperature'])
        self.labels=[self.tokenize(c) for c in 'ABCDEFGHIJ']
        if any(len(t)!=1 for t in self.labels):raise JudgeError('answer_label_not_single_token')
        self.label_ids=[t[0] for t in self.labels]

    def tokenize(self,text,*,special=False):
        value=text.encode('utf-8')
        capacity=max(32,len(value)+8)
        buf=(self.L.llama_token*capacity)()
        size=self.L.llama_tokenize(self.vocab,value,len(value),buf,capacity,False,special)
        if size<0:raise JudgeError('tokenization_failed')
        return list(buf[:size])

    def prompt_tokens(self,text,question,options):
        if not 2<=len(options)<=10:raise JudgeError('invalid_options')
        if self.model_id.startswith('decider'):
            # Publisher tokenizes the state and question separately, without BOS.
            head=self.tokenize('Context:\n'+text)
            tail='\n\nQuestion: '+question+'\nOptions:'
            tail+=''.join('\n('+chr(65+i)+') '+v for i,v in enumerate(options))
            tail+='\nAnswer: ('
            return head+self.tokenize(tail)
        prompt=('Read the state and question. Choose the single best option using the '
                'supplied information. Respond with exactly one option letter and no other text.\n\n'
                'State:\n'+text+'\n\nQuestion:\n'+question+'\n\nOptions:\n'+
                '\n'.join(chr(65+i)+'. '+v for i,v in enumerate(options)))
        # Native Ministral chat, no system prompt. User data never parses as control tokens.
        return self.tokenize('<s>[INST]',special=True)+self.tokenize(prompt)+self.tokenize('[/INST]',special=True)

    def evaluate(self,text,question,choices,*,use_cache=True):
        if not isinstance(choices,dict) or len(set(choices))!=len(choices):raise JudgeError('invalid_choices')
        key=hashlib.sha256(json.dumps([self.revision,self.temperature,text,question,choices],ensure_ascii=False).encode()).hexdigest()
        started=time.perf_counter()
        with self.lock:
            if use_cache and key in self.cache:
                result=dict(self.cache[key]);result['cached']=True;result['latency_ms']=(time.perf_counter()-started)*1000
                return result
            ids=self.prompt_tokens(text,question,list(choices.values()))
            if len(ids)>self.n_ctx:raise JudgeError('input_token_limit')
            L=self.L
            L.llama_memory_clear(L.llama_get_memory(self.ctx),True)
            for offset in range(0,len(ids),1024):
                chunk=ids[offset:offset+1024]
                b=self.batch;b.n_tokens=len(chunk)
                for i,token in enumerate(chunk):
                    b.token[i]=token;b.pos[i]=offset+i;b.n_seq_id[i]=1;b.seq_id[i][0]=0
                    b.logits[i]=(offset+i==len(ids)-1)
                if L.llama_decode(self.ctx,b)!=0:raise JudgeError('inference_failed')
            logits=L.llama_get_logits_ith(self.ctx,-1)
            if not logits:raise JudgeError('missing_logits')
            values=np.array([logits[t] for t in self.label_ids[:len(choices)]],dtype=np.float64)
            if not np.isfinite(values).all():raise JudgeError('nonfinite_logits')
            values=(values-values.max())/self.temperature
            probs=np.exp(values);probs/=probs.sum()
            scores=dict(zip(choices,map(float,probs)))
            result=dict(choice=max(scores,key=scores.get),scores=scores,model_id=self.model_id,
                model_revision=self.revision,runtime=self.runtime,input_tokens=len(ids),
                latency_ms=round((time.perf_counter()-started)*1000,3),cached=False)
            if use_cache:
                if len(self.cache)>=256:self.cache.pop(next(iter(self.cache)))
                self.cache[key]=result
            return result

    def close(self):
        if self.batch is not None:self.L.llama_batch_free(self.batch);self.batch=None
        if self.ctx:self.L.llama_free(self.ctx);self.ctx=None
        if self.model:self.L.llama_model_free(self.model);self.model=None
        self.cache.clear()

class JudgePool:
    """One resident model; serial access bounds unified-memory pressure."""
    def __init__(self):self.current=None;self.lock=threading.Lock()
    def evaluate(self,model_id,text,question,choices,**kwargs):
        with self.lock:
            if self.current is None or self.current.model_id!=model_id:
                if self.current:self.current.close();self.current=None
                if model_id=='decider-4b-dlp-v1':
                    from .mlx_backend import MLXJudge
                    self.current=MLXJudge(model_id)
                else:self.current=LocalJudge(model_id)
            return self.current.evaluate(text,question,choices,**kwargs)
    def close(self):
        with self.lock:
            if self.current:self.current.close();self.current=None
