"""Temporary authenticated loopback API with one MLX worker and a bounded queue."""
import argparse,hmac,json,queue,socket,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import mlx.core as mx
from small_judges import ROOT,NAMES,SmallJudge

p=argparse.ArgumentParser();p.add_argument('model',choices=NAMES);p.add_argument('--adapted',action='store_true');p.add_argument('--port',type=int,default=8332);a=p.parse_args()
key=(ROOT/'.local/small-judge-eval.key').read_text().strip();jobs=queue.Queue(maxsize=16);ready=threading.Event();state={}
def worker():
    try:
        adapter=ROOT/'.local/finetune/small-judges-adapted-bf16'/a.model if a.adapted else None
        judge=SmallJudge(a.model,bits=0,adapter=adapter);state['model']=a.model;ready.set()
    except Exception as error:
        state['load_error']=type(error).__name__;ready.set();return
    while True:
        job=jobs.get()
        if job is None:return
        payload,event,box,submitted=job
        try:
            if time.perf_counter()-submitted>30:raise TimeoutError('queue_timeout')
            began=time.perf_counter();criteria=payload['choices']
            if not isinstance(criteria,dict) or len(criteria)!=3 or any(not isinstance(k,str) or not isinstance(v,str) for k,v in criteria.items()):raise ValueError('invalid_choices')
            if not isinstance(payload['text'],str) or not isinstance(payload['question'],str):raise ValueError('invalid_text')
            ids=judge.encode(payload['text'],payload['question'],criteria);probs=judge.predict_ids(ids);scores=dict(zip(criteria,probs))
            box['value']={'choice':max(scores,key=scores.get),'scores':scores,'input_tokens':len(ids),'output_tokens':0,'model':a.model,'adapted':a.adapted,'cached':False,'queue_ms':(began-submitted)*1000,'inference_ms':(time.perf_counter()-began)*1000,'gpu_active_bytes':mx.get_active_memory(),'gpu_peak_bytes':mx.get_peak_memory()}
            box['status']=200
        except Exception as error:
            box.update(status=422 if isinstance(error,(ValueError,KeyError)) else 503,value={'error':str(error) if isinstance(error,(ValueError,TimeoutError)) else 'inference_failed'})
        finally:event.set();jobs.task_done()

thread=threading.Thread(target=worker,daemon=True);thread.start();ready.wait(120)
if 'load_error' in state or not ready.is_set():raise RuntimeError('Model failed to load: '+str(state.get('load_error')))
class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def setup(self):
        super().setup();self.connection.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
    def log_message(self,*args):pass
    def send(self,status,value):
        b=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def authorized(self):return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+key)
    def do_GET(self):
        self.send(200,{'ready':True,'model':a.model,'adapted':a.adapted}) if self.path=='/health' else self.send(404,{'error':'not_found'})
    def do_POST(self):
        if not self.authorized():self.close_connection=True;self.send(401,{'error':'unauthorized'});return
        if self.path!='/v1/decide':self.close_connection=True;self.send(404,{'error':'not_found'});return
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=256*1024:raise ValueError('body_size')
            payload=json.loads(self.rfile.read(n));event=threading.Event();box={};jobs.put_nowait((payload,event,box,time.perf_counter()))
        except queue.Full:self.send(503,{'error':'judge_busy'});return
        except (ValueError,TypeError):self.close_connection=True;self.send(400,{'error':'invalid_request'});return
        if not event.wait(35):self.send(504,{'error':'judge_timeout'});return
        self.send(box['status'],box['value'])
print(json.dumps({'event':'ready','model':a.model,'adapted':a.adapted,'port':a.port}),flush=True)
ThreadingHTTPServer(('127.0.0.1',a.port),Handler).serve_forever()
