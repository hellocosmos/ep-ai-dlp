"""Optional candidate must not enable itself or change incumbent policy defaults."""
import pytest
from aidlp_judge.mlx_backend import MLXJudge,MODEL_ID,candidate_metadata
from aidlp_judge.defaults import default_policies
from aidlp_judge.errors import JudgeError

def test_candidate_requires_explicit_server_opt_in(monkeypatch):
    monkeypatch.delenv('AIDLP_ENABLE_MLX_CANDIDATE',raising=False)
    with pytest.raises(JudgeError,match='model_not_enabled'):MLXJudge(MODEL_ID)

def test_incumbent_defaults_and_candidate_metadata():
    assert all(p.model_id=='decider-4b' for p in default_policies())
    metadata=candidate_metadata()
    assert metadata['experimental'] is True
    assert metadata['production_default'] is False
    assert metadata['context_limit']==768
    assert 'path' not in metadata
