"""Apply structured edits and validate before/after fixtures. No model inference.

python fixture_tools.py validate
python fixture_tools.py materialize T01 --out snapshots/T01
"""
import argparse
import collections
import copy
import hashlib
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def readjl(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()]

def writejl(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def locate_item(corpus,logical_id):
    hits=[(r,it) for r in corpus.values() for it in r.get('items',[]) if it['logical_id']==logical_id]
    assert len(hits)==1,(logical_id,len(hits))
    return hits[0]

def refresh(row):
    lines=[row['label']+('('+row['title']+')' if row['title'] else '')]
    for it in row.get('items',[]):
        p,h,m=it['path']
        if m: prefix='가나다라마바사아자차카타파하'[m-1]+'. '
        elif h: prefix=str(h)+'. '
        elif p: prefix=('①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳'[p-1] if p<=20 else str(p)+'항')+' '
        else: prefix=''
        lines.append(prefix+it['text'])
    row['text']='\n'.join(lines)

def apply_ops(original,ops):
    corpus=copy.deepcopy(original)
    for op in ops:
        kind=op['op']
        if kind in ['replace','rewrite','delete_item']:
            row,it=locate_item(corpus,op['logical_item_id'])
            if kind=='replace':
                assert it['text'].count(op['old'])==1,(op,it['text'])
                it['text']=it['text'].replace(op['old'],op['new'],1)
            elif kind=='rewrite':
                assert it['text']==op['expected_old'],op
                it['text']=op['new']
            else:
                assert it['text']==op['expected_old'],op
                it['text']='삭제'; it['deleted']=True
            refresh(row)
        elif kind=='renumber':
            rows=[r for r in corpus.values() if r['logical_id']==op['logical_article_id']]
            assert len(rows)==1
            row=rows[0]; oldid=row['id']
            assert row['label']==op['old_label']
            assert op['new_id'] not in corpus,op
            del corpus[oldid]
            row['id']=op['new_id']; row['label']=op['new_label']
            for it in row.get('items',[]):
                assert it['id'].startswith(oldid+'-')
                it['id']=row['id']+it['id'][len(oldid):]
            if row.get('items'): refresh(row)
            corpus[row['id']]=row
        elif kind=='replace_text':
            row=next(r for r in corpus.values() if r['logical_id']==op['logical_unit_id'])
            assert row['kind']=='appendix' and row['text'].count(op['old'])==1,(op,row['id'])
            row['text']=row['text'].replace(op['old'],op['new'],1)
        else: raise ValueError(kind)
    return corpus

def overlay(base,snapshot):
    return [{'id':i,'_delete':True} for i in sorted(set(base)-set(snapshot))]+[snapshot[i] for i in sorted(snapshot) if snapshot[i]!=base.get(i)]

def load_overlay(base,rows):
    data=copy.deepcopy(base)
    for r in rows:
        if r.get('_delete'):
            assert r['id'] in data
            del data[r['id']]
        else: data[r['id']]=copy.deepcopy(r)
    return data


def main():
    import argparse
    ap=argparse.ArgumentParser()
    ap.add_argument('case_id'); ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    base={r['id']:r for r in readjl(ROOT/'base_corpus.jsonl')}
    q=next(q for q in readjl(ROOT/'inputs.jsonl') if q['id']==args.case_id)
    for state in ['before','after']:
        data=load_overlay(base,readjl(ROOT/q[state+'_overlay']))
        writejl(args.out/(state+'.jsonl'),[data[k] for k in sorted(data)])
if __name__=='__main__': main()
