# Clef-Flash synthetic evaluation snapshot

Historical results from the 2026-10-04 original BF16 checkpoint evaluation: 428 calibration decisions and 736 test decisions. The rows contain synthetic case IDs, labels, scores and runtime metadata, not customer documents. This is not a new run or production acceptance.

The files are byte-for-byte copies of the retained local evidence. `snapshot-sha256.json` verifies this snapshot. `provenance.json` records hashes at evaluation time, including the original report before later cleanup and Git integration edits; its report hash is not the current Markdown report hash. Paths under `.local/` identify retained local experiment scripts and are not files distributed in this snapshot.

Weights, adapters, raw runtime logs, credentials and machine-specific deployment files are excluded. Model weights were removed during user-requested cleanup; repeating inference requires downloading the pinned checkpoint again. See [the evaluation report](../../CLEF_FLASH_EVALUATION.md) for the model revision, runtime and measurement limitations.
