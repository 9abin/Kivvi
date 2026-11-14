"""Retry only schema-invalid responses, without reading gold or selecting scores.

Use after run_state_compare.py has stopped. Previously accepted responses are
never regenerated. Malformed responses remain in the local cache and audit log.
The same prompt, model, and request body are sent again, at most twice.
"""
import argparse,json
from pathlib import Path
import run_state_compare as variant

def valid(value):
    if not isinstance(value,dict) or not isinstance(value.get('issues'),list): return False
    return all(isinstance(i,dict) and i.get('status') in ['new','resolved','persisting']
               and i.get('kind') in ['value_mismatch','obligation_mismatch','broken_citation']
               and all(k in i for k in ['left_article_id','right_article_id','summary']) for i in value['issues'])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--env',type=Path,default=variant.HERE/'.env')
    ap.add_argument('--results',type=Path,default=variant.HERE/'runs/state-compare-v2')
    ap.add_argument('--cache',type=Path,default=variant.HERE/'.cache');args=ap.parse_args()
    if not args.results.resolve().is_relative_to((variant.HERE/'runs').resolve()): raise ValueError('Use runs/.')
    m=variant.baseline.m; path=args.results/'predictions.jsonl'
    done={(r['id'],r['method']) for r in m.jl(path)} if path.exists() else set()
    api=m.OpenAIClient(args.env,args.cache);logpath=args.results/'format-validation-retries.jsonl'
    log=m.jl(logpath) if logpath.exists() else []
    try:
        for record in m.jl(args.results/'retrieval.jsonl'):
            for method in variant.baseline.METHODS:
                if (record['id'],method) in done: continue
                inp={'instruction':record['instruction'],'amendment':record['amendment'],
                     'before':record['states']['before']['visible'][method],
                     'after':record['states']['after']['visible'][method]}
                prompt=variant.PROMPT+json.dumps(inp,ensure_ascii=False)
                for attempt in range(3):
                    result,info=api.generate(prompt)
                    if valid(result): break
                    if attempt==2: raise RuntimeError('Response schema still invalid; no accepted output replaced.')
                    cache=args.cache/('openai-'+info['cache_key']+'.json')
                    archive=cache.with_suffix('.schema-invalid-'+str(len(log)+1)+'.json')
                    if archive.exists(): raise RuntimeError('Archive already exists; preserve it.')
                    cache.rename(archive)
                    log.append({'id':record['id'],'method':method,'reason':'response_schema_invalid',
                                'rejected_result':result,'cache_key':info['cache_key'],
                                'retry_policy':'same exact request; no gold or correctness selection'})
                    m.writejl(logpath,log)
                print(json.dumps({'id':record['id'],'method':method,'schema_valid_cached_response_ready':True}),flush=True)
    finally:
        (args.results/'format-retry-api-usage.json').write_text(json.dumps({'calls':api.calls,'usage':api.tokens},indent=2),encoding='utf-8')
        api.db.close()

if __name__=='__main__':main()
