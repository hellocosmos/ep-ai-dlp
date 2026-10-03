"""Isolated inference process: hard deadlines cannot leave a late allow in flight."""
import multiprocessing as mp
import threading
from .errors import JudgeError

def _serve(pipe):
    from .backends import JudgePool
    pool=JudgePool()
    try:
        while True:
            request=pipe.recv()
            if request is None:break
            model,text,question,choices=request
            try:pipe.send(('ok',pool.evaluate(model,text,question,choices)))
            except Exception as exc:
                reason=str(exc) if isinstance(exc,JudgeError) else 'inference_unavailable'
                pipe.send(('error',reason))
    except (EOFError,BrokenPipeError):pass
    finally:pool.close();pipe.close()

class JudgeProcess:
    def __init__(self,timeout=30):
        self.timeout=timeout;self.lock=threading.Lock();self.proc=None;self.pipe=None
    def _start(self):
        context=mp.get_context('spawn')
        parent,child=context.Pipe()
        self.proc=context.Process(target=_serve,args=(child,),daemon=True)
        self.proc.start();child.close();self.pipe=parent
    def _stop(self):
        if self.proc:
            if self.proc.is_alive():self.proc.terminate()
            self.proc.join(timeout=3)
            if self.proc.is_alive():self.proc.kill();self.proc.join(timeout=3)
            self.proc.close();self.proc=None
        if self.pipe:self.pipe.close();self.pipe=None
    def evaluate(self,model,text,question,choices):
        if not self.lock.acquire(timeout=0.1):raise JudgeError('judge_busy')
        try:
            if self.proc is None or not self.proc.is_alive():self._stop();self._start()
            self.pipe.send((model,text,question,choices))
            if not self.pipe.poll(self.timeout):self._stop();raise JudgeError('judge_timeout')
            kind,value=self.pipe.recv()
            if kind!='ok':raise JudgeError(value)
            return value
        except (EOFError,BrokenPipeError,OSError):
            self._stop();raise JudgeError('judge_process_unavailable') from None
        finally:self.lock.release()
    def close(self):
        with self.lock:self._stop()
