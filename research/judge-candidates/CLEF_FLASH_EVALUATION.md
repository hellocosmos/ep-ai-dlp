# Clef-Flash 9B: original-checkpoint DLP evaluation

Measured on 2026-10-04 on an Apple M4 Max with 36 GiB unified memory. This evaluates the published checkpoint without DLP fine-tuning. No running service, Windows enforcement path, model default or deployment was changed. The intended product deployment remains a company-hosted on-premises judgment API; this Mac is an evaluation host.

## Result

Clef-Flash correctly classified **653/736 (88.72%)** decisions. Korean: **86.49%**; English: **90.98%**. Raw sensitive-to-safe classifications: **21/288**. This count excludes sensitive examples predicted as insufficient, which remain review candidates.

| Model | Condition | Accuracy | Raw sensitive to safe / 288 |
|---|---|---:|---:|
| Jeff Qwen3.5 2B | Published original | 82.74% | 52 |
| Jeff Qwen3.5 2B | DLP LoRA | 89.95% | 24 |
| Jeff Gemma4 E2B | Published original | 83.83% | 5 |
| Jeff Gemma4 E2B | DLP LoRA | 91.17% | 14 |
| Clef-Flash 9B | Published original | 88.72% | 21 |

The baseline rows replay saved 2026-10-03 results. Their test IDs and labels were checked against this run. Original and DLP-adapted checkpoints are different conditions; this is not a controlled comparison of parameter count alone.

## Calibration-only policy routing

The existing gate-selection algorithm was fixed before evaluation. Threshold values use only the separate 428-row calibration set: allow **0.7199**, block **0.8326**. The threshold profile was saved before the first test prediction was written. Thresholds were not repaired using test labels. The same algorithm was used for the saved baselines. All actions below are offline recommendations, not actual enforcement.

- Allow: 224; block: 229; review: 283.
- Automatic coverage: **453/736 (61.55%)**.
- Sensitive allowed: 1; insufficient evidence allowed: 5.
- Safe blocked: 4; insufficient evidence blocked: 0.
- Total observed routing errors: **10**. Calibration performance is not a guarantee of future safety.

## Error observations

15 of the 21 raw sensitive-to-safe errors occurred in the customer-records policy. Fourteen of the 21 occurred in examples tagged with embedded instructions; this is an association within the synthetic test, not an isolated causal measurement of injection success. A larger model alone did not remove domain-policy errors.

## Policy breakdown

| Policy | Correct | Accuracy | Raw sensitive to safe |
|---|---:|---:|---:|
| commercial_terms | 117/139 | 84.17% | 0 |
| customer_records | 112/138 | 81.16% | 15 |
| hr_records | 106/124 | 85.48% | 1 |
| personal_sensitive | 104/106 | 98.11% | 1 |
| proprietary_technical | 118/123 | 95.93% | 4 |
| strategic_finance | 96/106 | 90.57% | 0 |

## Runtime and measurement limits

- Published BF16 backbone and joint schema head, using the reviewed publisher implementation. Model revision: `17f0b0ad64efb65d273590632833508766b2aae6`.
- PyTorch 2.14.1, Transformers 5.17.0, MPS/Metal. Every model parameter was verified on `mps:0`.
- Text-only, one policy question per forward pass; no generated answer tokens, no result cache. Test input length: 242–421 tokens. Encoding was audited for truncation; no input was truncated.
- Warm direct-forward latency: p50 **991.3 ms**, p95 **1261.4 ms**. Tokenization, HTTP transport and production concurrency are outside this timing. Six development warm-up cases precede quality measurements.
- PyTorch reference kernels are used for causal convolution and gated delta attention. These are not the optimized CUDA kernels used on an NVIDIA inference server.
- Parameters occupy **19.06 GB**; maximum sampled MPS driver allocation is **19.44 GB**. The Torch allocator counter alone excludes memory-mapped backbone storage and is not total model memory. Driver allocation is not whole-system memory or a production capacity recommendation.
- Earlier models used an MLX loopback API harness. Their timings must not be interpreted as a controlled speed comparison with this direct PyTorch run.

## Evidence and scope

The reused synthetic test has 736 decisions from 54 source-document families, comprising 370 Korean and 366 English decisions. Its labels include 310 normal, 288 sensitive and 138 insufficient-evidence cases. Calibration and test source families are disjoint. This is a retrospective comparison on already-used synthetic data, not independent customer-document acceptance. It does not test images, long documents, Windows integration or a physical on-premises GPU server.

- [Verified metrics and saved-baseline comparison](results/clef-flash/comparison.json)
- [Per-decision test predictions](results/clef-flash/original-mps-test.jsonl)
- [Calibration predictions](results/clef-flash/original-mps-calibration.jsonl)
- [Frozen calibration gates](results/clef-flash/frozen-gates.json)
- [Input audit](results/clef-flash/input-audit.json)
- [Runtime](results/clef-flash/runtime-mps.json)
- [Provenance](results/clef-flash/provenance.json)
- [Official model card](https://huggingface.co/Cloudflare/clef-flash/tree/17f0b0ad64efb65d273590632833508766b2aae6)

The downloaded checkpoint weights were removed during user-requested cleanup on 2026-10-04. Model configuration, pinned revision, download manifests and reviewed inference code remain under `.local/clef-flash/`; rerunning inference requires downloading the weights again. The isolated evaluation and reporting scripts are under `.local/clef-flash/`; no model weights or experiment results have been published by this task.
