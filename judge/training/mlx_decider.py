"""Decider's native answer-slot readout with Apple MLX training support."""
from pathlib import Path
import json
import mlx.core as mx
import mlx.nn as nn
from mlx.utils import tree_flatten
from mlx_lm.utils import load_model
from mlx_lm.models.qwen3_5 import TextModel,TextModelArgs
from mlx_lm.tuner.utils import linear_to_lora_layers,load_adapters
from transformers import AutoTokenizer

ROOT=Path(__file__).resolve().parents[2]
BASE=ROOT/'.local/finetune/decider-4b-bf16'

class DeciderTextModel(TextModel):
    def sanitize(self,weights):
        normalized={k.replace("model.language_model.","model.",1) if k.startswith("model.language_model.") else k:v for k,v in weights.items()}
        return super().sanitize(normalized)

def load_base(bits=4):
    model,config=load_model(BASE,get_model_classes=lambda config: (DeciderTextModel,TextModelArgs),lazy=False)
    if bits:
        nn.quantize(model,group_size=64,bits=bits)
        mx.eval(model.parameters())
    tokenizer=AutoTokenizer.from_pretrained(BASE,trust_remote_code=False)
    return model,tokenizer,config

def encode(tokenizer,text,question,options):
    # Data resembling control tokens never acquires control-token semantics.
    head=tokenizer.encode('Context:\n'+text,add_special_tokens=False,split_special_tokens=True)
    tail='\n\nQuestion: '+question+'\nOptions:'+''.join('\n('+chr(65+i)+') '+v for i,v in enumerate(options))+'\nAnswer: ('
    return head+tokenizer.encode(tail,add_special_tokens=False,split_special_tokens=True)

def option_weights(model,tokenizer):
    encoded=[tokenizer.encode(c,add_special_tokens=False) for c in 'ABC']
    if any(len(v)!=1 for v in encoded):raise ValueError('option label is not a single token')
    ids=[v[0] for v in encoded]
    head=model.model.embed_tokens if model.args.tie_word_embeddings else model.lm_head
    if isinstance(head,(nn.QuantizedEmbedding,nn.QuantizedLinear)):
        return mx.dequantize(head.weight[mx.array(ids)],head.scales[mx.array(ids)],head.biases[mx.array(ids)],group_size=head.group_size,bits=head.bits)
    return head.weight[mx.array(ids)]

def logits(model,inputs,lengths,weights):
    # Right padding follows the readout, so it cannot influence causal positions.
    hidden=model.model(inputs)
    final=hidden[mx.arange(inputs.shape[0]),lengths-1]
    return final.astype(mx.float32)@weights.astype(mx.float32).T

def attach_lora(model,layers=8,rank=8,scale=16):
    model.freeze()
    cfg={'rank':rank,'scale':scale,'dropout':0.0,'keys':['self_attn.q_proj','self_attn.v_proj','self_attn.o_proj','linear_attn.in_proj_qkv','linear_attn.out_proj','mlp.gate_proj','mlp.up_proj','mlp.down_proj']}
    linear_to_lora_layers(model,layers,cfg)
    model.train()
    # Frozen prefix can retain optimized kernels; gradients start at adapted layers.
    for layer in model.layers[:-layers]:layer.eval()
    return {'fine_tune_type':'lora','num_layers':layers,'lora_parameters':cfg}


def attach_saved_adapter(model,folder):
    cfg=json.loads((Path(folder)/'adapter_config.json').read_text())
    attach_lora(model,layers=cfg['num_layers'],rank=cfg['lora_parameters']['rank'],scale=cfg['lora_parameters']['scale'])
    load_adapter_weights(model,Path(folder)/'adapters.safetensors')
    model.eval()


def load_adapter_weights(model,path):
    weights=mx.load(str(path))
    expected=dict(tree_flatten(model.trainable_parameters()))
    if set(weights)!=set(expected):raise ValueError('adapter parameter set mismatch')
    if any(weights[k].shape!=expected[k].shape for k in expected):raise ValueError('adapter parameter shape mismatch')
    # The frozen base remains intact; strict validation above covers every adapter leaf.
    model.load_weights(list(weights.items()),strict=False)
