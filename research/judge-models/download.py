"""Download explicitly pinned public checkpoints; no remote Python execution."""
import json
from pathlib import Path
from huggingface_hub import snapshot_download
ROOT = Path(__file__).resolve().parents[2]
MODELS = [
 ('decider-2b','Mapika/decider-2b-GGUF','ff2e5e687327eda9ac34e9a3ca84d3f400672c87','decider-2b-v11-Q4_K_M.gguf'),
 ('decider-4b','Mapika/decider-4b-GGUF','b79f09d9ba7837f1b744295ea267b55d08e958ec','decider-4b-v2.1-Q4_K_M.gguf'),
 ('standardone-3b','StandardThinking/StandardOne-3B-GGUF','6f3f88a58c5354cd02473df380425e921ff34076','StandardOne-3B-Q4_K_M.gguf'),
]
manifest = []
for name, repo, revision, weight in MODELS:
 path = ROOT / '.local' / 'judge-models' / name
 print('Downloading',name,revision,flush=True)
 snapshot_download(repo_id=repo,revision=revision,local_dir=path,allow_patterns=[weight,'*.json','*.jinja','README.md','LICENSE','NOTICE','SHA256SUMS'])
 manifest.append(dict(id=name,repository=repo,revision=revision,filename=weight,quantization='Q4_K_M'))
 (ROOT/'research/judge-models/manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
 print('Ready',name,flush=True)
