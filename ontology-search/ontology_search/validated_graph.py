"""Read the reviewed DB, including all user original leave edges.

This adapter does not re-extract/overwrite curated graph edges with regexes.
It maps the reviewed source bindings onto an applied before/after corpus.
No benchmark labels or expected counterpart lists are opened.
"""
import collections,re,sqlite3
from pathlib import Path

class DomainGraph:
    def __init__(self,path=None):
        path=Path(path) if path else Path(__file__).resolve().parents[1]/'data/ontology.db'
        c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True); c.row_factory=sqlite3.Row
        self.parents={r['name']:r['parent'] for r in c.execute('select name,parent from concept')}
        bindings={r['item_id']:dict(r) for r in c.execute('select * from source_binding')}
        attrs=collections.defaultdict(set)
        for r in c.execute('select edge_id,attr from edge_attr'):
            attrs[r['edge_id']].update(x for x in re.split(r'[·/]',r['attr']) if x)
        self.edges=[]
        for r in c.execute('''select e.id,e.item_id,e.concept,e.rel,v.temporal_scope,v.review_status
          from edge_item_concept e left join review_evidence v on v.edge_id=e.id'''):
            binding=bindings[r['item_id']]; original=binding['origin']=='user_original'
            active=original or (r['review_status']=='source_checked_ai' and r['temporal_scope']=='current_text')
            self.edges.append({**dict(r),'logical_item_id':binding['logical_item_id'],'logical_article_id':binding['logical_article_id'],'attrs':attrs[r['id']],'original':original,'active':active})
        self.heads=[]
        for r in c.execute('select * from edge_head'):
            self.heads.append({'child':bindings[r['child_item_id']]['logical_item_id'],'parent':bindings[r['parent_item_id']]['logical_item_id'],'concept':r['concept']})
        c.close()
        self.stats={'all_edges':len(self.edges),'user_original_edges':sum(e['original'] for e in self.edges),'source_checked_active_new_edges':sum(e['active'] and not e['original'] for e in self.edges),'all_head_bindings':len(self.heads)}

    def related(self,a,b):
        if a==b: return 3
        # Only actual concept hierarchy; never connect all personnel/pay/leave
        # just because they belong to a broad domain category.
        for child,parent in [(a,b),(b,a)]:
            seen=set(); p=self.parents.get(child)
            while p and p not in seen:
                if p==parent: return 1
                seen.add(p); p=self.parents.get(p)
        return 0

    def rank(self,corpus,anchors,query,lex,seed_items=None):
        current={r.get('logical_id',r['id']):r for r in corpus.values()}
        available_items={i.get('logical_id',i['id']) for r in corpus.values() for i in r.get('items',[]) if not i.get('deleted')}
        def available(e):
            row=current.get(e['logical_article_id'])
            return bool(e['active'] and row and (not row.get('items') or e['logical_item_id'] in available_items))
        edges=[e for e in self.edges if available(e)]
        excluded={r['id'] for r in anchors}; anchor_logical={r.get('logical_id',r['id']) for r in anchors}
        explicit=set(seed_items or [])
        if not explicit: explicit={e['logical_item_id'] for e in edges if e['logical_article_id'] in anchor_logical}
        # Respect concept-scoped head bindings, not a global bag of attributes.
        linked=collections.defaultdict(set)
        for h in self.heads:
            if h['parent'] in explicit: linked[h['concept']].add(h['child'])
            if h['child'] in explicit: linked[h['concept']].add(h['parent'])
        seed=[e for e in edges if e['logical_article_id'] in anchor_logical and (e['logical_item_id'] in explicit or e['logical_item_id'] in linked[e['concept']])]
        if not seed: seed=[e for e in edges if e['logical_article_id'] in anchor_logical]
        by_concept=collections.defaultdict(set)
        for e in seed: by_concept[e['concept']].update(e['attrs'])
        scores={}; paths=collections.defaultdict(list)
        for e in edges:
            aid=current[e['logical_article_id']]['id']
            if aid in excluded: continue
            best=0; best_overlap=set(); seed_concept=None
            for concept,seed_attrs in by_concept.items():
                rel=self.related(concept,e['concept']); overlap=e['attrs']&seed_attrs
                if not rel or (seed_attrs and not overlap): continue
                weight=rel+min(len(overlap),4)*.5+{'근거':.3,'정의':.2,'언급':.1}[e['rel']]
                if weight>best: best=weight; best_overlap=overlap; seed_concept=concept
            if not best: continue
            scores[aid]=max(scores.get(aid,0),best)
            paths[aid].append({'seed_concept':seed_concept,'concept':e['concept'],'attrs':sorted(best_overlap),'rel':e['rel'],'logical_item_id':e['logical_item_id'],'origin':'user_original' if e['original'] else 'ai_source_review'})
        return sorted(scores,key=lambda aid:(-scores[aid],-lex.get(aid,0),aid)),dict(paths)
