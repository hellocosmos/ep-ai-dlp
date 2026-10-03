# Small decision models: original versus DLP adaptation

**Deployment context:** the judgment LLM is intended to run behind a company-hosted, on-premises API. Employee endpoints enforce policy and do not need these model weights or the Apple Silicon training/runtime environment. The Mac measurements below are development experiments, not production server sizing or proof of Windows integration. See the [deployment architecture](../../docs/architecture.md).

Completed on 2026-10-03. All three models were actually trained and all six original/adapted conditions were measured through the same local API. No production default was changed.

The adapted Jeff Qwen3.5 2B is the preferred candidate for the next deployment-oriented qualification: 89.95% test accuracy, 86.9 ms median API response, and 3.83 GB peak active GPU allocation during the quality run. Adapted Jeff Gemma4 E2B achieved the highest observed accuracy, 91.17%, but used 9.37 GB and took 121.2 ms. Its nine extra correct answers were all in the Korean subset; the English correct counts were identical. This small synthetic difference does not establish general superiority.

## Test accuracy and API response

| Model | Condition | Correct / 736 | Accuracy | Korean / 370 | English / 366 | API p50 / p95, ms | Active GPU, GB |
|---|---|---:|---:|---:|---:|---:|---:|
| Decider 2B | original | 490 | 66.58% | 62.70% | 70.49% | 66.3 / 99.8 | 3.80 |
| Decider 2B | adapted | 602 | 81.79% | 80.27% | 83.33% | 68.3 / 102.3 | 3.81 |
| Jeff Qwen3.5 2B | original | 609 | 82.74% | 78.92% | 86.61% | 87.0 / 119.0 | 3.79 |
| Jeff Qwen3.5 2B | adapted | 662 | 89.95% | 87.03% | 92.90% | 86.9 / 120.2 | 3.83 |
| Jeff Gemma4 E2B | original | 617 | 83.83% | 80.81% | 86.89% | 121.4 / 173.6 | 9.36 |
| Jeff Gemma4 E2B | adapted | 671 | 91.17% | 89.46% | 92.90% | 121.2 / 171.8 | 9.37 |

GPU figures are decimal GB of MLX active allocation, not total host memory. The measured test inputs are short: 132–311 tokens for Decider, 193–372 for Jeff Qwen, and 191–373 for Jeff Gemma, including questions and option descriptions. All models use BF16; see the precision decision below.

| Model | Accuracy gain, percentage points | Wrong → correct | Correct → wrong | Raw sensitive → safe, original → adapted |
|---|---:|---:|---:|---:|
| Decider 2B | +15.22 | 131 | 19 | 106 → 52 |
| Jeff Qwen3.5 2B | +7.20 | 57 | 4 | 52 → 24 |
| Jeff Gemma4 E2B | +7.34 | 78 | 24 | 5 → 14 |

Gemma adaptation improved aggregate accuracy while increasing raw sensitive-to-safe errors from 5 to 14. Raw argmax classification must not directly determine DLP allow/block. All error counts above concern classifications before confidence routing.

## Calibration-only routing, replayed on test

| Model | Condition | Block | Allow | Review | Sensitive allowed | Insufficient allowed | Safe blocked | Insufficient blocked |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Decider 2B | original | 16 | 134 | 586 | 7 | 2 | 0 | 0 |
| Decider 2B | adapted | 107 | 189 | 440 | 5 | 3 | 0 | 3 |
| Jeff Qwen3.5 2B | original | 127 | 76 | 533 | 0 | 0 | 1 | 0 |
| Jeff Qwen3.5 2B | adapted | 132 | 140 | 464 | 0 | 0 | 0 | 0 |
| Jeff Gemma4 E2B | original | 116 | 133 | 487 | 0 | 2 | 0 | 0 |
| Jeff Gemma4 E2B | adapted | 222 | 235 | 279 | 0 | 4 | 3 | 3 |

Adapted Jeff Qwen automatically handled 272/736 decisions (36.96%) with no observed routing errors, leaving 464 for review. Adapted Jeff Gemma automatically handled 457/736 (62.09%) but made 10 routing errors: four insufficient-evidence allows, three safe blocks, and three insufficient-evidence blocks. These are different coverage operating points, not a matched-coverage proof that one model is intrinsically safer. No test-driven threshold repair was applied. All “actions” are offline recommendations; no enforcement occurred.

## Length and load probes

| Model | Condition | Short p50, ms | Medium p50, ms | Long p50, ms | 4 clients p95, ms | 6 sequential policies p50, ms |
|---|---|---:|---:|---:|---:|---:|
| Decider 2B | original | 63.3 | 122.9 | 277.2 | 252.9 | 712.9 |
| Decider 2B | adapted | 64.5 | 126.6 | 279.9 | 247.9 | 725.7 |
| Jeff Qwen3.5 2B | original | 84.8 | 144.8 | 297.4 | 342.8 | 830.9 |
| Jeff Qwen3.5 2B | adapted | 83.6 | 134.3 | 302.5 | 324.1 | 798.9 |
| Jeff Gemma4 E2B | original | 121.8 | 203.3 | 433.1 | 499.7 | 1211.4 |
| Jeff Gemma4 E2B | adapted | 122.6 | 202.3 | 435.1 | 501.4 | 1217.4 |

All six conditions completed 1,164 calibration/test requests, one warmup, 80 length/concurrency requests, and 60 requests in ten six-policy groups: 7,830 inference requests total, with no failures. Six unauthenticated requests separately returned the expected HTTP 401. Each condition has 20 samples per length/concurrency group; the four-client test uses five batches. Inputs use the same repeated Korean text lengths across models, not identical token counts. The “long” probes are only 862–943 tokens across models; they do not qualify full-length corporate documents or 8K+ contexts. The six-policy probe resends the state for each policy and has no shared-prefix optimization. These measurements are one sequential local run, not a randomized benchmark across hardware or optimized serving engines.

## Training and selected checkpoints

| Model | Development original → selected | Selected step / 1,438 | Training + in-loop dev, seconds | Trainable parameters | Peak MLX training GB |
|---|---:|---:|---:|---:|---:|
| Decider 2B | 74.44% → 87.36% | 719 | 473.0 | 598,016 | 4.71 |
| Jeff Qwen3.5 2B | 87.36% → 91.29% | 719 | 608.6 | 598,016 | 4.78 |
| Jeff Gemma4 E2B | 89.33% → 95.79% | 1438 | 934.3 | 843,776 | 10.34 |

Training durations exclude model loading, token preparation, and the initial baseline development evaluation. Qwen selected the first full epoch; Gemma selected the second. There was no test-based training or checkpoint selection.

## Pinned checkpoints

- [Mapika/decider-2b](https://huggingface.co/Mapika/decider-2b/tree/533964dae8be954c5b5e19fa4948e48408094c1e), revision `533964dae8be954c5b5e19fa4948e48408094c1e`.
- [mstrasser/Jeff-Qwen3.5-2B](https://huggingface.co/mstrasser/Jeff-Qwen3.5-2B/tree/00448e884cce9e34396d8c72e9a692b41383b1ee), revision `00448e884cce9e34396d8c72e9a692b41383b1ee`.
- [mstrasser/Jeff-Gemma4-E2B](https://huggingface.co/mstrasser/Jeff-Gemma4-E2B/tree/e3de3e99a979f92afd995d4e526c7e3170ae49cf), revision `e3de3e99a979f92afd995d4e526c7e3170ae49cf`.

## Recommendation and limits

Use adapted Jeff Qwen3.5 2B as the primary candidate for the next local DLP qualification and retain adapted Jeff Gemma4 E2B as the accuracy/coverage alternative. Decider 2B improved the most but remained behind both Jeff variants in this test. Before an operational decision, use fresh source-family-held-out documents with realistic formatting, long inputs, adversarial instructions, and a common error budget or matched automation coverage. This benchmark has 54 synthetic test families and has been reused in earlier comparisons; it is not an independent production acceptance test. This is Mac MLX qualification only; Windows/CPU serving and exported runtimes are untested. No model is installed into the current enforcement path by this experiment.
## Scope and method

This experiment compares `Mapika/decider-2b`, `mstrasser/Jeff-Qwen3.5-2B`, and `mstrasser/Jeff-Gemma4-E2B`, each before and after additional local DLP training. “Original” means the publisher's already-trained decision checkpoint, not the general-purpose Qwen or Gemma foundation model.

All six conditions use the publisher BF16 backbone with a float32 decision readout, MLX/Metal on the same M4 Max Mac with 36 GiB unified memory, and the same authenticated loopback HTTP harness. Option probabilities use temperature 1; action thresholds are fitted separately. Each model retains its own tokenizer, publisher prompt, and trained decision readout. The Gemma MLX port is an experimental local implementation, not a claim of upstream Jeff MLX support.

The frozen corpus contains 2,874 training, 356 development, 428 calibration, and 736 test decisions across six policies and English/Korean text. Source families are separated across splits. The test has 54 source families, 288 sensitive, 310 safe, and 138 insufficient-evidence decisions; 370 are Korean and 366 English. These translated/policy variants are not 736 statistically independent source documents. The test has been used in earlier model comparisons: these are retrospective synthetic benchmarks, not fresh independent acceptance or customer-document validation. No corpus contents are uploaded.

Adaptation uses last-decoder-layer LoRA, rank/scale 16, batch size 4, two epochs, seed 20261003, AdamW with peak learning rate 2e-5, label smoothing 0.03, and gradient clipping 1.0. The best development accuracy selects the adapter. Calibration/test labels do not select training checkpoints. Choice order is shuffled during training. Parameter counts vary with architecture even though the training recipe is matched.

All three loaders matched publisher prompt tokenization on six development cases covering all policies. Their predicted choices matched an independent Transformers CPU float32 reference in all six cases per model; maximum probability differences were 0.00688/0.00737/0.00733 for Decider/Qwen/Gemma. Serial-versus-padded-batch checks stayed below 0.03 probability difference. These bounded checks qualify the experimental port on sampled inputs; they are not exhaustive numerical equivalence. All three also completed three real optimizer steps with finite loss and gradients. Qwen variants adapt 598,016 parameters; Gemma adapts 843,776.

Gates use calibration only: allow/block thresholds are set above the highest observed wrong prediction, rounded to the next 0.0001, with at least three supporting source families. No supporting operating point disables that action. Reported test error counts do not establish a future zero-error guarantee.

Each quality run uses one warmup, batch size one, no response/prefix cache, no text generation, and a 2,048-token input limit with rejection instead of truncation. API timings include tokenization, queueing, model execution, and HTTP overhead. Separate probes measure short/medium/long text, four concurrent clients on a single bounded inference queue, and six sequential policy questions. GPU allocation is not total process RSS or an exact deployment RAM requirement.

## Precision decision

A development-only Decider-2B check found BF16 accuracy of 74.44%, versus 54.78% after MLX affine Q4 quantization. The completed Q4 adaptation pilot is retained separately. Its improvement would mix domain learning with recovery from quantization loss, so the primary six-condition comparison uses BF16. No test outcome informed this change. This result does not imply that every Q4 format or implementation loses the same accuracy.

## Related video experiments

- [Dantae Lab's local decision-model comparison](https://www.youtube.com/watch?v=_H_5Zz1hb0Q): a Korean synthetic DLP task with 200 documents, including benign documents that resemble sensitive ones. At a reported 95% sensitive-document recall, the leading local candidates falsely flagged 8 of 140 benign documents. The author explicitly says the threshold was selected on those same 200 cases; the operating point is exploratory. Noise and formatting changes shifted recall and showed why a fixed threshold needs qualification. The recommended rule/model/human routing resembles our architecture. See the [evaluation](https://www.youtube.com/watch?v=_H_5Zz1hb0Q&t=947s) and [limitations](https://www.youtube.com/watch?v=_H_5Zz1hb0Q&t=1315s).
- [Code Factory's Laya fine-tuning experiment](https://www.youtube.com/watch?v=ZktfWXfOwIY): Korean six-category customer-support classification. On a 250-case test, reported Laya accuracy improved from 32.8% to 86.4% after 1,000 training examples. A separate 10,000-example training run reported 598/600 correct on a different test. The latter is not a matched comparison with Jev's reported 250/250. The useful hypothesis is that small task-specific models can benefit substantially from adaptation; it does not establish their accuracy on our DLP policies.

Both summaries were checked against the original Korean video transcripts. Their reported numbers are not measurements performed in this workspace.

## Published evidence

The [committed synthetic result snapshot](results/small-bf16/) contains per-case scores, aggregate results, latency probes and the measured environment. Raw runtime logs, keys, weights and adapters are excluded; local machine paths in metadata are normalized.

## Evidence and reproduction

Primary evidence: `evidence/judge/small-comparison-bf16/`. Loader/reference qualification and the separate Q4 pilot: `evidence/judge/small-comparison/`. Pinned source manifests and verified checkpoints: `.local/finetune/small-judges/`. Selected BF16 adapters: `.local/finetune/small-judges-adapted-bf16/`.

The isolated scripts are under `judge/training/`: `small_judges.py`, `qualify_small.py`, `reference_small.py`, `train_small.py`, `serve_small.py`, `run_small_api.py`, `evaluate_small_api.py`, and `compare_small.py`. Training and evaluation deliberately refuse to overwrite existing experiment artifacts. Review output paths before repeating a run. No production routing or default-model change is part of this experiment.
