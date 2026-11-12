"""Transactional personal reading graph. No model calls or external network."""
from __future__ import annotations
import hashlib, json, os, re, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, model_validator

ID = r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,99}$'
class Source(BaseModel):
    label: str = Field(min_length=1,max_length=300)
    url: str | None = Field(default=None,max_length=2000)
    message_id: str | None = Field(default=None,max_length=200)
    @model_validator(mode='after')
    def safe_url(self):
        if self.url and not self.url.startswith(('https://','http://')):
            raise ValueError('Only http(s) source URLs are supported')
        return self
class Node(BaseModel):
    id: str = Field(pattern=ID)
    title: str = Field(min_length=1,max_length=200)
    body: str = Field(min_length=1,max_length=12000)
    kind: Literal['book','concept','insight','method','question','example']
    attribution: Literal['user','ai','external']
    status: Literal['open','proposed','accepted','verified'] = 'open'
    quote: str = Field(default='',max_length=8000)
    quote_attribution: Literal['user','ai','external'] | None = None
    topics: list[str] = Field(default_factory=list,max_length=20)
    @model_validator(mode='after')
    def epistemics(self):
        if self.status=='verified' and self.attribution!='external':
            raise ValueError('Verified is reserved for externally sourced claims')
        return self
class Edge(BaseModel):
    id: str = Field(pattern=ID)
    source: str = Field(pattern=ID)
    target: str = Field(pattern=ID)
    relation: Literal['origin','analogy','supports','extends','challenges','revises','example','applies','part_of']
    reason: str = Field(min_length=1,max_length=3000)
    attribution: Literal['user','ai','external'] = 'ai'
    @model_validator(mode='after')
    def nonself(self):
        if self.source==self.target: raise ValueError('Self edges are not useful')
        return self
class Capture(BaseModel):
    request_id: str = Field(pattern=ID)
    expected_revision: int = Field(ge=0)
    source: Source
    nodes: list[Node] = Field(default_factory=list,max_length=60)
    edges: list[Edge] = Field(default_factory=list,max_length=120)

class Conflict(ValueError): pass

def now(): return datetime.now(timezone.utc).isoformat()
def dumps(x): return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def default_path():
    base=os.environ.get('READING_BETWEEN_DATA_DIR') or os.environ.get('PLUGIN_DATA')
    return Path(base or Path.home()/'.local/share/reading-between')/'reading.sqlite3'

class Store:
    def __init__(self,path=None):
        self.path=Path(path) if path else default_path()
        self.path.parent.mkdir(parents=True,exist_ok=True)
        existed=self.path.exists()
        with self._db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS graph(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS changes(id INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT NOT NULL UNIQUE,digest TEXT NOT NULL,action TEXT NOT NULL,created_at TEXT NOT NULL,before_data TEXT NOT NULL,after_data TEXT NOT NULL,summary TEXT NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO graph VALUES(1,0,?)',(dumps({'nodes':[],'edges':[]}),))
        if not existed:self.path.chmod(0o600)
    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=15)
        db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try: yield db; db.commit()
        except Exception: db.rollback(); raise
        finally: db.close()
    def _state(self,db):
        row=db.execute('SELECT revision,data FROM graph WHERE id=1').fetchone()
        return row['revision'],json.loads(row['data'])
    def _view(self,db,include_archived=False):
        rev,g=self._state(db)
        if not include_archived:
            g['nodes']=[n for n in g['nodes'] if not n.get('archived')]
            ids={n['id'] for n in g['nodes']}
            g['edges']=[e for e in g['edges'] if e['source'] in ids and e['target'] in ids]
        recent=[dict(r) for r in db.execute('SELECT id,action,created_at,summary FROM changes ORDER BY id DESC LIMIT 20')]
        for r in recent:r['summary']=json.loads(r['summary'])
        return {**g,'revision':rev,'recent_changes':recent}
    def view(self,include_archived=False):
        with self._db() as db:return self._view(db,include_archived)
    def _mutate(self,request_id,expected_revision,payload,action,fn):
        if not re.fullmatch(ID,request_id):raise ValueError('Invalid request_id')
        digest=hashlib.sha256(dumps({'action':action,'payload':payload}).encode()).hexdigest()
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=db.execute('SELECT id,digest FROM changes WHERE request_id=?',(request_id,)).fetchone()
            if previous:
                if previous['digest']!=digest:raise Conflict('request_id reused with different content')
                return {'replayed':True,'change_id':previous['id'],'graph':self._view(db)}
            rev,g=self._state(db)
            if rev!=expected_revision:raise Conflict(f'Revision conflict: expected {expected_revision}, current {rev}; reload map')
            before=dumps(g)
            summary=fn(g,db)
            after=dumps(g)
            if before==after:return {'replayed':False,'changed':False,'graph':self._view(db)}
            cur=db.execute('INSERT INTO changes(request_id,digest,action,created_at,before_data,after_data,summary) VALUES(?,?,?,?,?,?,?)',(request_id,digest,action,now(),before,after,dumps(summary)))
            db.execute('UPDATE graph SET revision=?,data=? WHERE id=1',(rev+1,after))
            return {'replayed':False,'changed':True,'change_id':cur.lastrowid,'summary':summary,'graph':self._view(db)}
    def capture(self,args:Capture):
        payload=args.model_dump(exclude={'request_id','expected_revision'})
        def apply(g,db):
            incoming=[n.id for n in args.nodes]
            if len(incoming)!=len(set(incoming)):raise ValueError('Duplicate node IDs in batch')
            if len(args.edges)!=len({e.id for e in args.edges}):raise ValueError('Duplicate edge IDs in batch')
            nmap={n['id']:n for n in g['nodes']};emap={e['id']:e for e in g['edges']}
            summary={'added_nodes':[],'updated_nodes':[],'added_edges':[],'updated_edges':[]}
            for item in args.nodes:
                n=item.model_dump();old=nmap.get(n['id']);provenance={**args.source.model_dump(),'quote':n['quote'],'attribution':n['attribution'],'quote_attribution':n['quote_attribution'] or n['attribution']}
                if n['status']=='verified' and not args.source.url:raise ValueError('Verified claims require an external source URL')
                if old and old.get('archived'):raise ValueError('Archived node cannot be silently revived; undo archive or use a new ID')
                sources=list(old.get('sources',[])) if old else []
                if provenance not in sources:sources.append(provenance)
                n.update(sources=sources,archived=False)
                if old and n=={k:v for k,v in old.items() if k not in ('created_at','updated_at')}:
                    continue
                n['created_at']=old['created_at'] if old else now();n['updated_at']=now();nmap[n['id']]=n
                summary['updated_nodes' if old else 'added_nodes'].append(n['id'])
            for item in args.edges:
                e=item.model_dump()
                for endpoint in (e['source'],e['target']):
                    if endpoint not in nmap or nmap[endpoint].get('archived'):raise ValueError(f'Edge endpoint not found: {endpoint}')
                old=emap.get(e['id'])
                if old and e=={k:v for k,v in old.items() if k!='sources'}:continue
                e['sources']=list(old.get('sources',[])) if old else []
                provenance=args.source.model_dump()
                if provenance not in e['sources']:e['sources'].append(provenance)
                emap[e['id']]=e;summary['updated_edges' if old else 'added_edges'].append(e['id'])
            g['nodes']=list(nmap.values());g['edges']=list(emap.values());return summary
        return self._mutate(args.request_id,args.expected_revision,payload,'capture',apply)
    def revise(self,node_id,title,body,status,request_id,expected_revision):
        def apply(g,db):
            n=next((x for x in g['nodes'] if x['id']==node_id and not x.get('archived')),None)
            if not n:raise ValueError('Node not found')
            patch=Node.model_validate({**{k:v for k,v in n.items() if k in Node.model_fields},'title':title,'body':body,'status':status}).model_dump()
            if all(n.get(k)==v for k,v in patch.items()):return {}
            if status=='verified' and not any(s.get('url') for s in n['sources']):raise ValueError('Verified claims require an external source URL')
            n.update(patch,updated_at=now());return {'updated_nodes':[node_id]}
        return self._mutate(request_id,expected_revision,{'id':node_id,'title':title,'body':body,'status':status},'revise',apply)
    def archive(self,node_id,request_id,expected_revision):
        def apply(g,db):
            n=next((x for x in g['nodes'] if x['id']==node_id),None)
            if not n:raise ValueError('Node not found')
            if n.get('archived'):return {}
            n['archived']=True;n['updated_at']=now();return {'archived_nodes':[node_id]}
        return self._mutate(request_id,expected_revision,{'id':node_id},'archive',apply)
    def undo(self,change_id,request_id,expected_revision):
        def apply(g,db):
            last=db.execute('SELECT * FROM changes ORDER BY id DESC LIMIT 1').fetchone()
            if not last or last['id']!=change_id:raise Conflict('Only the latest change can be undone; reload history')
            if last['action']=='undo':raise ValueError('An undo cannot itself be undone in this version')
            restored=json.loads(last['before_data']);g.clear();g.update(restored)
            return {'undone_change':change_id}
        return self._mutate(request_id,expected_revision,{'id':change_id},'undo',apply)
    def search(self,query,limit=12):
        if not query.strip():return {'nodes':[],'note':'Supply words or concepts to search'}
        terms=[x.lower() for x in re.split(r'[\s,，、;；]+',query.strip()) if x]
        view=self.view();scored=[]
        for n in view['nodes']:
            title=n['title'].lower();body=(n['body']+' '+n.get('quote','')+' '+' '.join(n.get('topics',[]))).lower()
            score=sum(3*(t in title)+(t in body) for t in terms)
            if score:scored.append((score,n))
        scored.sort(key=lambda x:(-x[0],x[1]['id']))
        return {'revision':view['revision'],'nodes':[n for _,n in scored[:max(1,min(limit,30))]],'note':'Lexical candidates only; let the host model judge semantic relationships. No embedding API.'}
