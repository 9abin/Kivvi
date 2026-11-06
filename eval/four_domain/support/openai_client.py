"""OpenAI Responses/Embeddings client for the explicitly authorized evaluation."""
import hashlib
import os
import json
import re
import sqlite3
import time
import urllib.error
import urllib.request

class OpenAIClient:
    def __init__(self,env,cache):
        cfg={k:v for k,v in os.environ.items() if k.startswith('OPENAI_')}
        for line in (env.read_text(encoding='utf-8-sig').splitlines() if env.exists() else []):
            m=re.match(r'^\s*(?:export\s+)?(OPENAI_[A-Z_]+)\s*=\s*(.*?)\s*$',line)
            if m:
                v=m[2]
                if len(v)>1 and v[0]==v[-1] and v[0] in ['"',"'"]: v=v[1:-1]
                cfg[m[1]]=v
        self.key=cfg.get('OPENAI_API_KEY','')
        if not self.key: raise ValueError('OPENAI_API_KEY is missing')
        self.model=cfg.get('OPENAI_MODEL','gpt-5.6-luna')
        self.embed_model=cfg.get('OPENAI_EMBED_MODEL','text-embedding-3-small')
        self.effort=cfg.get('OPENAI_REASONING_EFFORT','low')
        self.base=cfg.get('OPENAI_BASE_URL','https://api.openai.com/v1').rstrip('/')
        self.interval=float(cfg.get('OPENAI_MIN_INTERVAL','0.25'))
        self.cache=cache; cache.mkdir(parents=True,exist_ok=True)
        self.last=0; self.calls=0; self.tokens={}
        self.db=sqlite3.connect(cache/'openai-embeddings.sqlite')
        self.db.execute('create table if not exists vectors (key text primary key,value blob)')

    def request(self,endpoint,body=None):
        for attempt in range(4):
            delay=self.interval-(time.monotonic()-self.last)
            if delay>0: time.sleep(delay)
            req=urllib.request.Request(self.base+'/'+endpoint,data=json.dumps(body,ensure_ascii=False).encode('utf-8') if body is not None else None,headers={'Content-Type':'application/json','Authorization':'Bearer '+self.key})
            self.last=time.monotonic()
            try:
                with urllib.request.urlopen(req,timeout=240) as response: data=json.loads(response.read())
                self.calls+=1
                for k,v in data.get('usage',{}).items():
                    if isinstance(v,int): self.tokens[k]=self.tokens.get(k,0)+v
                return data
            except urllib.error.HTTPError as e:
                raw=e.read().decode('utf-8',errors='replace').replace(self.key,'[REDACTED]')
                if e.code in [429,500,502,503,504] and attempt<3:
                    time.sleep(min(30,5*(attempt+1))); continue
                raise RuntimeError(f'OpenAI HTTP {e.code}: {raw[:1200]}') from None

    def embed(self,texts,task='RETRIEVAL_DOCUMENT',progress=None):
        import numpy as np
        keys=[hashlib.sha256((self.embed_model+'|768|'+t).encode()).hexdigest() for t in texts]
        values={}
        for k in set(keys):
            row=self.db.execute('select value from vectors where key=?',(k,)).fetchone()
            if row: values[k]=np.frombuffer(row[0],dtype=np.float32).copy()
        missing={k:t for k,t in zip(keys,texts) if k not in values}; todo=list(missing)
        for offset in range(0,len(todo),64):
            batch=todo[offset:offset+64]
            response=self.request('embeddings',{'model':self.embed_model,'input':[missing[k] for k in batch],'dimensions':768,'encoding_format':'float'})
            data=sorted(response['data'],key=lambda v:v['index']); assert len(data)==len(batch)
            for k,v in zip(batch,data):
                a=np.asarray(v['embedding'],dtype=np.float32); a=a/max(float(np.linalg.norm(a)),1e-12)
                values[k]=a; self.db.execute('insert or replace into vectors values (?,?)',(k,a.tobytes()))
            self.db.commit()
            if progress: progress(min(offset+64,len(todo)),len(todo))
        return np.stack([values[k] for k in keys])

    def generate(self,prompt):
        body={'model':self.model,'input':prompt,'text':{'format':{'type':'json_object'}},'reasoning':{'effort':self.effort},'max_output_tokens':10000,'store':False}
        key=hashlib.sha256(json.dumps(body,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        path=self.cache/('openai-'+key+'.json')
        if path.exists(): response=json.loads(path.read_text(encoding='utf-8'))
        else:
            response=self.request('responses',body)
            path.write_text(json.dumps(response,ensure_ascii=False),encoding='utf-8')
        if response.get('status')!='completed': raise RuntimeError('OpenAI response incomplete; inspect local cache')
        text=''.join(c.get('text','') for item in response.get('output',[]) for c in item.get('content',[]) if c.get('type')=='output_text')
        try: value=json.loads(text)
        except json.JSONDecodeError: raise RuntimeError('OpenAI returned invalid JSON; inspect local cache') from None
        return value,{'cache_key':key,'finish_reason':response.get('status'),'usage':response.get('usage',{}),'reasoning_effort':self.effort}
