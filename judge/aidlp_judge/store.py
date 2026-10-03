"""Local transactional policies, bound approvals and metadata-only audit."""
import json
import sqlite3
import time
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from .defaults import default_policies
from .schemas import Policy

class Conflict(ValueError):
    pass

class Store:
    def __init__(self, path: Path):
        self.path = path
        self.dispatch_lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS policies(revision INTEGER PRIMARY KEY,payload TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY,kind TEXT NOT NULL,payload TEXT NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,digest TEXT NOT NULL,revision INTEGER NOT NULL,status TEXT NOT NULL,expires REAL NOT NULL,consumed REAL);
                CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,digest TEXT NOT NULL,scenario TEXT NOT NULL,content_hash TEXT NOT NULL,byte_count INTEGER NOT NULL,created REAL NOT NULL);
            ''')
            if not db.execute('SELECT 1 FROM policies').fetchone():
                db.execute('INSERT INTO policies VALUES(1,?,?)', (json.dumps([p.model_dump() for p in default_policies()],ensure_ascii=False),time.time()))
        path.chmod(0o600)

    @contextmanager
    def transaction(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def current(self):
        with self.transaction() as db:
            row=db.execute('SELECT * FROM policies ORDER BY revision DESC LIMIT 1').fetchone()
            return {'revision':row['revision'],'policies':json.loads(row['payload']),'created_at':row['created']}

    def publish(self, expected: int, policies: list[Policy]):
        with self.dispatch_lock:
            return self._publish(expected, policies)

    def _publish(self, expected: int, policies: list[Policy]):
        with self.transaction() as db:
            latest=db.execute('SELECT max(revision) FROM policies').fetchone()[0]
            if latest!=expected: raise Conflict('policy_revision_conflict')
            revision=latest+1
            db.execute('INSERT INTO policies VALUES(?,?,?)',(revision,json.dumps([p.model_dump() for p in policies],ensure_ascii=False),time.time()))
            self._audit(db,'policy.published',{'revision':revision,'policy_ids':[p.id for p in policies]})
        return self.current()

    def _audit(self,db,kind,payload):
        db.execute('INSERT INTO audit VALUES(?,?,?,?)',(str(uuid.uuid4()),kind,json.dumps(payload,ensure_ascii=False),time.time()))

    def audit(self,kind,payload):
        with self.transaction() as db:self._audit(db,kind,payload)

    def events(self):
        with self.transaction() as db:
            return [dict(id=r['id'],kind=r['kind'],data=json.loads(r['payload']),created_at=r['created']) for r in db.execute('SELECT * FROM audit ORDER BY created DESC LIMIT 200')]

    def create_approval(self,digest,revision):
        key=str(uuid.uuid4())
        with self.transaction() as db:
            db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,NULL)',(key,digest,revision,'pending',time.time()+600))
            self._audit(db,'approval.created',{'id':key,'request_digest':digest,'policy_revision':revision})
        return key

    def approvals(self):
        with self.transaction() as db:
            return [dict(r) for r in db.execute('SELECT * FROM approvals ORDER BY rowid DESC LIMIT 100')]

    def approve(self,key,approved:bool):
        with self.transaction() as db:
            row=db.execute('SELECT * FROM approvals WHERE id=?',(key,)).fetchone()
            if not row or row['status']!='pending' or row['expires']<time.time(): raise Conflict('approval_unavailable')
            status='approved' if approved else 'denied'
            db.execute('UPDATE approvals SET status=? WHERE id=?',(status,key))
            self._audit(db,'approval.'+status,{'id':key})

    def consume_approval(self,key,digest,revision):
        with self.transaction() as db:
            row=db.execute('SELECT * FROM approvals WHERE id=?',(key,)).fetchone()
            valid=row and row['status']=='approved' and row['consumed'] is None and row['digest']==digest and row['revision']==revision and row['expires']>=time.time()
            if not valid:return False
            db.execute('UPDATE approvals SET consumed=? WHERE id=?',(time.time(),key))
            self._audit(db,'approval.consumed',{'id':key,'request_digest':digest})
            return True

    def record_delivery(self,key,digest,scenario,content_hash,byte_count):
        with self.transaction() as db:
            db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?)',(key,digest,scenario,content_hash,byte_count,time.time()))

    def deliveries(self):
        with self.transaction() as db:return [dict(r) for r in db.execute('SELECT * FROM deliveries ORDER BY created DESC LIMIT 100')]
