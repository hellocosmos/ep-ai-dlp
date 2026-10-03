# Bilingual AI DLP decision-model training

This pipeline trains an experimental Decider 4B adapter for policy-conditioned content classification. It does not learn organization permissions, approve a tool call, or transmit documents. The incumbent DLP API and Windows endpoint remain unchanged.

## Dataset

`data/v1/manifest.json` seals the 4,394 synthetic decisions: 2,874 training, 356 development, 428 calibration, and 736 final test rows. The six classes are customer records, private commercial terms, employee records, proprietary technical details, unreleased corporate finance/strategy, and person-linked sensitive records. Documents and policy questions each include Korean and English, including mixed-language requests.

There are 234 authored bilingual source families, split 108/36/36/54. Translations, name/number variations, embedded-instruction variants and cross-policy negatives stay in the source family's partition. Exact duplicate decision tuples are removed. Repeated variants are correlated and must not be reported as thousands of independent enterprise documents. Labels and rationale are authored by an agent, without independent human review. All names and records are synthetic; identity/account numbers use explicit TEST markers.

The previous 36-case test corpus is a legacy regression test, never a training source. Its wording has been inspected during earlier work, so it is not an unseen final test. The new final test is frozen before training, with checkpoint selection restricted to development data and thresholds restricted to calibration data.

## Runtime and training

Use macOS Apple Silicon and Python 3.12. Install the pinned dependencies into a separate environment:

```sh
uv venv judge/.train-venv --python 3.12
uv pip install --python judge/.train-venv/bin/python -r judge/training/requirements-training.lock.txt
```

The pinned BF16 base is `Mapika/decider-4b` revision `eb5fbdfc9448473ec25e399882912863afbdb70e`, locally in `.local/finetune/decider-4b-bf16`. `base-manifest.json` records its provenance. Model-supplied Python is not executed.

```sh
judge/.train-venv/bin/python judge/training/download_base.py
judge/.train-venv/bin/python judge/training/build_dataset.py
judge/.train-venv/bin/python judge/training/train.py --batch-size 4 --epochs 2 --layers 1 --rank 16
judge/.train-venv/bin/python judge/training/evaluate.py
judge/.train-venv/bin/python judge/training/finalize.py
```

Training quantizes the frozen base to MLX 4-bit/group-64 and adds rank-16, scale-16 LoRA to the final layer. Only the final A/B/C option-token logits receive cross-entropy supervision, with 0.03 label smoothing, randomized option order, gradient clipping and gradient checkpointing. No document text is generated. Inputs over 768 tokens fail explicitly; no truncation is used. Current synthetic examples are shorter than 312 tokens, so longer-document accuracy remains unqualified.

Best-development weights are saved in `.local/finetune/decider-4b-dlp-v1/adapters.safetensors`. The latest adapter, optimizer state and progress are also saved for recovery inspection; automatic mid-epoch continuation is not implemented. Run configuration, source revision, selection evidence and hashes accompany the adapter. Training should run alone to avoid memory pressure from concurrent models.

The comparison uses the same MLX quantization before and after adaptation. Existing GGUF Q4_K_M results are not a controlled baseline for this MLX run. Calibration thresholds are empirical gates on correlated synthetic data, not calibrated probabilities or a real-world error guarantee. Scores must be recalibrated after any conversion to GGUF or a different runtime.

## Offline inference

```sh
judge/.train-venv/bin/python judge/training/predict.py \
  --text-file /absolute/path/to/document.txt \
  --policy customer_records --policy-language ko
```

The command returns scores and never enforces an action. `--base` evaluates the unadapted MLX baseline. See `evidence/judge/finetune/comparison.json` for results and `FINETUNE_REPORT.md` for the completed assessment.

### Refined offline routing

`ROUTING_REPORT.md` records a follow-up routing study using the same adapter. Generate its profile and replay with:

```sh
python3 judge/training/refine_calibration.py
judge/.train-venv/bin/python judge/training/predict.py \
  --text-file /absolute/path/to/document.txt \
  --policy customer_records --policy-language ko \
  --routing-profile evidence/judge/routing-v2/profile.json
```

This adds `recommended_action` (`allow`, `block`, or `review`) to offline output. It never transmits or enforces. The profile is checked against the actual adapter/configuration, model revision and policy definitions; `--base` cannot use it. Gates are selected from calibration scores, with six-fold source-family validation. The previous final test is replayed retrospectively, not treated as a new independent holdout. Existing sealed artifacts and API/default policies are unchanged.

## Separate candidate API

The existing decision API supports this model only with explicit opt-in, in the MLX environment. Run a separate loopback instance:

```sh
AIDLP_ENABLE_MLX_CANDIDATE=1 PYTHONPATH=judge \
  judge/.train-venv/bin/python -m uvicorn aidlp_judge.jev_api:app \
  --host 127.0.0.1 --port 8312
```

This minimal training environment serves the MLX candidate; use the existing GGUF service for baseline model IDs. Use the existing bearer-token file locally and `model_id: decider-4b-dlp-v1` with the `aidlp-decision-v1` contract. Never paste the token into chat or logs. The adapter and configuration hashes are verified before model load; the revision binds them to the pinned base and runtime. Inference runs in an isolated worker with the existing deadline and busy handling. The DLP policy schema/defaults do not select this candidate; the optional service is for evaluation only. GPU-server/Linux deployment of this Apple MLX adapter requires a separate export and qualification step.

## Three small decision models: completed comparison

See the [original/fine-tuned results](../../research/judge-candidates/SMALL_MODEL_FINETUNING.md) for Decider 2B, Jeff Qwen3.5 2B and Jeff Gemma4 E2B. This follow-up reuses the final test retrospectively; it is not a new independent acceptance set. All six conditions use BF16, not the 4-bit setup described above.

On Apple Silicon with Python 3.12, use `requirements-small-judges.lock.txt` for the measured MLX environment. The independent CPU reference additionally requires the pinned candidate environment in `research/judge-candidates/requirements-lock.txt`; these are separate environments. Model download is explicit and requires substantial local disk space.

```sh
uv venv judge/.train-venv --python 3.12
uv pip install --python judge/.train-venv/bin/python -r judge/training/requirements-small-judges.lock.txt
uv venv .local/candidate-env --python 3.12
uv pip install --python .local/candidate-env/bin/python -r research/judge-candidates/requirements-lock.txt
.local/candidate-env/bin/python research/judge-candidates/download_small_judges.py
git clone https://github.com/firelex/jeff.git .local/jeff-research
git -C .local/jeff-research checkout d0173b4ee317a46dee031421b713f3fc5f868cfe
```

For each of `decider-2b`, `jeff-qwen-2b`, and `jeff-gemma-e2b`, run these commands sequentially, replacing `MODEL` with that identifier:

```sh
judge/.train-venv/bin/python judge/training/qualify_small.py MODEL
.local/candidate-env/bin/python judge/training/reference_small.py MODEL
judge/.train-venv/bin/python judge/training/train_small.py MODEL --smoke
judge/.train-venv/bin/python judge/training/train_small.py MODEL
```

Before API evaluation, provision a fresh random secret in `.local/small-judge-eval.key` with mode 0600 using your local secret-management workflow. Never copy or publish another machine's key. Run `run_small_api.py MODEL` and then `run_small_api.py MODEL --adapted` for each model, with no concurrent training. These own and stop a temporary authenticated loopback API on port 8332. Finally run `compare_small.py`. Training and evaluation intentionally refuse to overwrite existing artifacts; review output paths before re-running. The adapters remain local and are not shipped in this source repository.
