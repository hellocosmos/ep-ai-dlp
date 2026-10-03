"""Own one temporary API at a time and persist load/evaluation outcomes."""
import argparse,json,socket,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('model');p.add_argument('--adapted',action='store_true');a=p.parse_args()
condition='adapted' if a.adapted else 'original';out=ROOT/'evidence/judge/small-comparison-bf16'/a.model
with socket.socket() as s:
    if s.connect_ex(('127.0.0.1',8332))==0:raise RuntimeError('Port 8332 occupied')
extra=['--adapted'] if a.adapted else []
command=[sys.executable,str(ROOT/'judge/training/serve_small.py'),a.model,*extra]
began=time.monotonic()
with (out/f'{condition}-server.log').open('w') as log:
    server=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
    try:
        for i in range(180):
            if server.poll() is not None:raise RuntimeError('Server exited: '+str(server.returncode))
            try:
                with urllib.request.urlopen('http://127.0.0.1:8332/health',timeout=2) as response:
                    if response.status==200:break
            except OSError:time.sleep(1)
        else:raise RuntimeError('Server readiness timeout')
        load_seconds=time.monotonic()-began
        request=urllib.request.Request('http://127.0.0.1:8332/v1/decide',data=b'{}',headers={'Content-Type':'application/json'})
        try:urllib.request.urlopen(request,timeout=5);raise RuntimeError('Missing authentication enforcement')
        except urllib.error.HTTPError as error:
            if error.code!=401:raise
        with (out/f'{condition}-evaluation.log').open('w') as evaluation:
            subprocess.run([sys.executable,str(ROOT/'judge/training/evaluate_small_api.py'),a.model,*extra],stdout=evaluation,stderr=subprocess.STDOUT,check=True)
        (out/f'{condition}-runtime.json').write_text(json.dumps({'command':command,'load_seconds':load_seconds,'authentication_check':401,'completed':True,'elapsed_seconds':time.monotonic()-began},indent=2)+'\n')
        print(json.dumps({'model':a.model,'condition':condition,'completed':True}),flush=True)
    finally:
        if server.poll() is None:
            server.terminate()
            try:server.wait(timeout=20)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=10)
