# Alternative decision model screening — 2026-10-03

The completed [small-model original/fine-tuned comparison](SMALL_MODEL_FINETUNING.md) measures Decider 2B, Jeff Qwen3.5 2B, and Jeff Gemma4 E2B in six matched BF16 API conditions. Adapted Jeff Qwen reached 89.95% and adapted Jeff Gemma 91.17% on the reused 736-case synthetic test; the report separates raw accuracy, calibrated routing errors, latency, and memory. These are additional experiments, not changes to production defaults.

The current Decider remains a useful candidate. Raw accuracy, confidence calibration, and automatic enforcement acceptance must be assessed separately. Rejecting a model solely because a strict gate sends most examples to review is not justified.

The subsequent [Winnow E4B Q8/Q6/Q5/Q4 comparison](WINNOW_QUANTIZATION.md) includes freshly measured Decider API baselines, calibration-only refined action gates, concurrency, and shared-state policy costs. Those operating points supersede the historical strict-gate count below for this new experiment. Winnow Q4 lost 4.08 percentage points against Q8 without a latency improvement on the measured Mac/Metal stack; no running-service defaults were changed.

## Local screening

Same frozen synthetic development set: 356 decisions, six policies, English/Korean documents and questions. This development set has already informed Decider tuning; this is candidate screening, not a fresh independent comparison. Questions and choice descriptions were passed unchanged. No confidence routing was applied.

| Model | Accuracy | Korean | English | Median inference |
|---|---:|---:|---:|---:|
| Decider baseline (previous saved run) | 87.92% | 85.39% | 90.45% | Not remeasured |
| Decider fine-tuned (previous saved run) | 92.98% | 91.01% | 94.94% | Not remeasured |
| Laya multilingual typed-decisions | 36.24% | 35.39% | 37.08% | 10.85 ms |
| GLiNER2.5-multi-Decide | 51.40% | 53.37% | 49.44% | 29.44 ms |

Laya used laya-mlx 0.3.0, float16, Metal. GLiNER used gliner2 2.0.0, Transformers 5.17.0, PyTorch MPS, float32; DeBERTa attention fell back to eager. Batch size one, one warmup excluded, serial runs. Timings compare these implementations, not equally optimized model architectures. All 356 result IDs and output labels were validated. Laya had no state, question, or option truncation: maximum question/option/head token counts were 108/34/196, below 256 head and 48 per-option limits. GLiNER passed text, choice descriptions, and policy prompt via its classification schema; default 4096 maximum is much larger than these short inputs.

The typed Laya variant and GLiNER checkpoint are poor zero-shot replacements on this specific task; this does not establish that their families cannot improve with adaptation. Confidence calibration and automatic actions were not evaluated for these candidates.

The preceding independent-of-training final test for Decider contained 736 synthetic decisions: baseline 82.34%, fine-tuned 87.50%. Do not confuse this with development accuracy. Its strict calibrated routing sent 719/736 (97.69%) to review. That is an operating-point result, not evidence that only 2.31% were classified correctly.

## Further candidates

- [Imajev-4B](https://huggingface.co/mohit67890/imajev-4b): Qwen3.5-4B instruction base with a decision readout and explicit abstention. Apache-2.0. A relevant next local candidate; Korean DLP accuracy is untested. Maker benchmark scores must not be compared numerically to our synthetic DLP scores.
- [Kev-4B](https://huggingface.co/jaredpalmer/kev-4b): Qwen3.5-4B-Base with a pointer decision head and MLX support. Apache-2.0; English-only validation makes Korean qualification necessary. Not locally tested.
- [Solar Decide](https://openrouter.ai/upstage/solar-decide): hosted Upstage decision endpoint, supports Jev-style state and typed questions; provider emphasizes Korean. A useful commercial comparator on the same DLP corpus. No API call, payment, or data submission performed.
- [GLiNER model card](https://huggingface.co/fastino/GLiNER2.5-multi-Decide) and [Laya typed model card](https://huggingface.co/alfred361/laya-multilingual-typed-decisions): exact checkpoints screened above, not all variants in their families.

Pinned model metadata is saved beside this report. Outputs, input-check evidence and aggregate metrics are under `evidence/judge/candidates/`. The isolated environment is `.local/candidate-env`; its initial Transformers 4.57.6 was incompatible with the GLiNER tokenizer config, resolved with 5.17.0. Existing serving/training environments and product defaults were not changed.

## Reproduce

```sh
.local/candidate-env/bin/python research/judge-candidates/screen.py laya
.local/candidate-env/bin/python research/judge-candidates/screen.py gliner
.local/candidate-env/bin/python research/judge-candidates/summarize.py
```

Use the environment lock and pinned model metadata. No model training is performed by these scripts.
