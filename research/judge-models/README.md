# Pinned decision models

The local pilot reads the logits of the next option letter and normalizes only the specified option scores. It does not ask a generative model to write a verdict and parse prose.

| Candidate | Pinned GGUF release | License reported by publisher | Local format |
|---|---|---|---|
| Decider 2B | [Mapika/decider-2b-GGUF](https://huggingface.co/Mapika/decider-2b-GGUF/tree/ff2e5e687327eda9ac34e9a3ca84d3f400672c87) | Apache-2.0 | Q4_K_M |
| Decider 4B | [Mapika/decider-4b-GGUF](https://huggingface.co/Mapika/decider-4b-GGUF/tree/b79f09d9ba7837f1b744295ea267b55d08e958ec) | Apache-2.0 | Q4_K_M |
| Standard One 3B | [StandardThinking/StandardOne-3B-GGUF](https://huggingface.co/StandardThinking/StandardOne-3B-GGUF/tree/6f3f88a58c5354cd02473df380425e921ff34076) | Apache-2.0; LICENSE and NOTICE downloaded | Q4_K_M |

`manifest.json` records the exact filenames/revisions. `download.py` downloads weights and metadata to `.local/judge-models/`; weights and credentials are not repository artifacts. No remote Python source is executed. Recheck notices before redistributing weights.

Decider uses the publisher's plain Context / Question / Options / Answer format, independent tokenization of context and question tail, and the checkpoint's `choice` temperature in `decider_config.json`. The source references are the pinned model cards and [Decider 4B source card](https://huggingface.co/Mapika/decider-4b).

Standard One uses native decision wording, native Mistral control tokens, no system prompt, and the 3B publisher's choice temperature 0.90. Reference: [Standard One 3B serving recipe](https://huggingface.co/StandardThinking/StandardOne-3B/blob/cac76f6ace34e4630ab9d9d2b551f7393f41f221/README.md). The base tokenizer revision is [Ministral 3 3B](https://huggingface.co/mistralai/Ministral-3-3B-Instruct-2512-BF16/tree/b6d637bef2393152b3da2b2fde72eecdee30557e).

A key compatibility detail: the Hugging Face Jinja path automatically inserts a lengthy default system message; the publisher's native `mistral-common` path does not. `tokenizer_parity.py` therefore checks Standard One against the pinned native Tekken tokenizer. All six ordinary/adversarial text checks match. Content resembling control tokens is encoded as ordinary user text; it never becomes a chat delimiter. Decider parity uses `split_special_tokens=True` for the same reason. This verifies adapter tokenization, not equivalence of quantized model accuracy to BF16 or resistance to semantic prompt injection.

The runtime is `llama-cpp-python==0.3.36`, compiled with Metal on this Mac. GPU offload support is checked at load. Prompt batches request only the final position logits; only one model is resident per inference worker. The service uses a 256-entry in-memory digest-keyed score cache. Cached scores do not cache identity, authorization, policy revision, or approval.

Evaluation is documented in [../../docs/local-judge.md](../../docs/local-judge.md). Publisher benchmarks are not reused as our task accuracy.
