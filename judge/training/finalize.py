"""Seal a completed adapter and its offline evaluation provenance."""
import hashlib,json
from train import OUT,EVIDENCE,save_json

def main():
    completion=json.loads((OUT/'completion.json').read_text())
    if not completion.get('completed'):raise RuntimeError('training incomplete')
    files=['adapters.safetensors','adapter_config.json','run-config.json','selection.json','completion.json','calibration.json']
    digests={name:hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in files}
    comparison=EVIDENCE/'comparison.json'
    revision=hashlib.sha256((digests['adapters.safetensors']+digests['adapter_config.json']+'eb5fbdfc9448473ec25e399882912863afbdb70e'+'MLX/Metal/4bit-group64/LoRA').encode()).hexdigest()
    save_json(OUT/'artifact-manifest.json',{'model_id':'decider-4b-dlp-v1','model_revision':revision,'adapter_sha256':digests['adapters.safetensors'],'files':digests,'comparison_sha256':hashlib.sha256(comparison.read_bytes()).hexdigest(),'base_model':'Mapika/decider-4b','base_revision':'eb5fbdfc9448473ec25e399882912863afbdb70e','runtime':'MLX/Metal/4bit-group64/LoRA','status':'experimental_offline_candidate','production_default_changed':False})
    print(json.dumps({'model_id':'decider-4b-dlp-v1','model_revision':revision,'adapter_sha256':digests['adapters.safetensors'],'sealed':True}))
if __name__=='__main__':main()
