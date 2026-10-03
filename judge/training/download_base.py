"""Fetch only pinned model data; remote repository Python is neither fetched nor run."""
import json
from pathlib import Path
from huggingface_hub import snapshot_download
ROOT=Path(__file__).resolve().parents[2]
def main():
    manifest=json.loads((Path(__file__).parent/'base-manifest.json').read_text())
    path=snapshot_download(repo_id=manifest['repository'],revision=manifest['revision'],local_dir=str(ROOT/'.local/finetune/decider-4b-bf16'),allow_patterns=['model.safetensors','config.json','decider_config.json','tokenizer.json','tokenizer_config.json','chat_template.jinja','README.md'])
    print(path)
if __name__=='__main__':main()
