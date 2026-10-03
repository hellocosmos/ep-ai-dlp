"""Run isolated, authenticated quantization benchmarks, owning each server process."""
import argparse,json,os,socket,subprocess,sys,time,urllib.request,urllib.error
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'evidence/judge/winnow'
p=argparse.ArgumentParser()
p.add_argument('quant',choices=['Q8_0','Q6_K','Q5_K_M','Q4_K_M'])
p.add_argument('--smoke-only',action='store_true')
a=p.parse_args()
with socket.socket() as s:
    if s.connect_ex(('127.0.0.1',8331))==0:raise SystemExit('Port 8331 is occupied')
keyfile=ROOT/'.local/winnow-eval.key'
key=keyfile.read_text().strip()
model=ROOT/f'.local/judge-candidates/Winnow-E4B/gguf/quantized/Winnow-E4B-{a.quant}.gguf'
command=[sys.executable,str(ROOT/'.local/winnow-inference/scripts/serve.py'),'--model',str(model),'--alias','Winnow-E4B','--text-only','--context','4096','--decision-context','4096','--decision-parallel','1','--chat-parallel','1','--threads','8','--port','8331','--head','selected','--api-key-file',str(keyfile)]
def child(script,args,label):
    with (OUT/f'{a.quant}-{label}.log').open('w') as log:
        subprocess.run([sys.executable,str(ROOT/'research/judge-candidates'/script),*args],stdout=log,stderr=subprocess.STDOUT,check=True)
    print(json.dumps({'quant':a.quant,'completed':label}),flush=True)
started=time.monotonic()
with (OUT/f'server-{a.quant}.log').open('w') as log:
    server=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
    try:
        for attempt in range(180):
            if server.poll() is not None:raise RuntimeError('Server exited: '+str(server.returncode))
            try:
                req=urllib.request.Request('http://127.0.0.1:8331/health',headers={'Authorization':'Bearer '+key})
                with urllib.request.urlopen(req,timeout=2) as response:
                    if response.status==200:break
            except (OSError,urllib.error.URLError):time.sleep(1)
        else:raise RuntimeError('Server readiness timeout')
        ready=time.monotonic()-started
        # A decision request without credentials must fail before model work.
        req=urllib.request.Request('http://127.0.0.1:8331/v1/systemone',data=b'{}',headers={'Content-Type':'application/json'})
        try:
            urllib.request.urlopen(req,timeout=5)
            raise RuntimeError('Unauthenticated decision request unexpectedly accepted')
        except urllib.error.HTTPError as error:
            if error.code!=401:raise
        print(json.dumps({'quant':a.quant,'pid':server.pid,'ready_seconds':ready,'unauthorized_status':401}),flush=True)
        common=['Winnow-E4B','--quant',a.quant]
        child('evaluate_winnow.py',common+['--split','dev','--limit','6','--suffix=-smoke'],'smoke')
        if not a.smoke_only:
            for split in ['calibration','test']:
                child('evaluate_winnow.py',common+['--split',split],split)
            child('winnow_api_latency.py',[a.quant],'latency')
        rss=subprocess.check_output(['ps','-o','rss=','-p',str(server.pid)],text=True).strip()
        (OUT/f'{a.quant}-runtime.json').write_text(json.dumps({'pid':server.pid,'ready_seconds':ready,'rss_kib_after_measurement':int(rss),'command':command,'total_seconds':time.monotonic()-started},indent=2)+'\n')
    finally:
        if server.poll() is None:
            server.terminate()
            try:server.wait(timeout=30)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=10)
