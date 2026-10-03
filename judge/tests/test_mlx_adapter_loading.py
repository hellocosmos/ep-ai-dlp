import sys
from pathlib import Path
import pytest
mx=pytest.importorskip('mlx.core')
import mlx.nn as nn
from mlx.utils import tree_flatten
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'training'))
from mlx_decider import load_adapter_weights

def test_roundtrip_and_reject_missing_parameters(tmp_path):
    model=nn.Linear(3,2);weights=dict(tree_flatten(model.trainable_parameters()))
    values={k:mx.ones_like(v) for k,v in weights.items()}
    path=tmp_path/'adapter.safetensors';mx.save_safetensors(str(path),values)
    load_adapter_weights(model,path)
    assert all(bool(mx.all(v==1)) for _,v in tree_flatten(model.trainable_parameters()))
    values.pop(next(iter(values)));mx.save_safetensors(str(path),values)
    with pytest.raises(ValueError,match='parameter set mismatch'):load_adapter_weights(model,path)

def test_reject_extra_or_wrong_shape(tmp_path):
    model=nn.Linear(3,2);weights=dict(tree_flatten(model.trainable_parameters()));path=tmp_path/'adapter.safetensors'
    mx.save_safetensors(str(path),weights|{'unexpected':mx.zeros((1,))})
    with pytest.raises(ValueError,match='parameter set mismatch'):load_adapter_weights(model,path)
    key=next(iter(weights));weights[key]=mx.zeros((1,))
    mx.save_safetensors(str(path),weights)
    with pytest.raises(ValueError,match='parameter shape mismatch'):load_adapter_weights(model,path)
