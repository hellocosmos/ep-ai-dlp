"""Generate four quantizations independently from one verified BF16 source."""
import hashlib,json,subprocess,time
from pathlib import Path
root=Path(__file__).resolve().parents[2];meta=json.loads((root/'research/judge-candidates/Winnow-E4B-metadata.json').read_text());entry=next(x for x in meta['files'] if x['rfilename'].endswith('-BF16.gguf'))
source=root/'.local/judge-candidates/Winnow-E4B'/entry['rfilename'];dest=source.parent/'quantized';dest.mkdir(exist_ok=True)
out=root/'evidence/judge/winnow';binary=root/'.local/winnow-inference/.build/bin/llama-quantize'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()
if source.stat().st_size!=entry['size'] or sha(source)!=entry['lfs']['sha256']:raise RuntimeError('Invalid source')
report={'source_repository':meta['id'],'source_revision':meta['sha'],'source_sha256':entry['lfs']['sha256'],'quantizer_sha256':sha(binary),'files':{}}
for quant in ['Q8_0','Q6_K','Q5_K_M','Q4_K_M']:
 target=dest/f'Winnow-E4B-{quant}.gguf'
 if target.exists():raise RuntimeError('Refusing to overwrite '+str(target))
 command=[str(binary),'--max-buffer-size','512',str(source),str(target),quant,'8'];start=time.monotonic()
 with (out/f'quantize-{quant}.log').open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True)
 report['files'][quant]={'path':str(target),'size':target.stat().st_size,'sha256':sha(target),'seconds':time.monotonic()-start,'command':command}
 (out/'quantized-manifest.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({'quant':quant,'size':target.stat().st_size,'seconds':time.monotonic()-start}),flush=True)
