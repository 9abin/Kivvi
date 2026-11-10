import json
import sqlite3
from pathlib import Path

from ontology_search.domain_key_graph import DomainGraph
from ontology_search.validated_graph import DomainGraph as LegacyGraph

HERE=Path(__file__).resolve().parents[1]/'data'


def fixture_graph(tmp_path):
    db=tmp_path/'graph.db'
    c=sqlite3.connect(db)
    c.executescript((HERE/'schema.sql').read_text(encoding='utf8'))
    for name,parent in [('휴가',None),('특별휴가','휴가'),('보수',None),('인사',None)]:
        c.execute('insert into concept(name,parent) values(?,?)',(name,parent))
    specs=[('seed','휴가','절차'),('seed','보수','대상'),
           ('vacation','휴가','절차'),('pay','보수','대상'),
           ('shared','휴가','절차'),('shared','보수','대상'),('shared','인사','절차'),
           ('personnel','인사','절차'),('wrong_vacation','휴가','대상'),
           ('wrong_pay','보수','절차'),('child','특별휴가','절차')]
    ids=sorted({s[0] for s in specs})
    corpus={i:{'id':i,'items':[{'id':i+'-item'}]} for i in ids}
    for i in ids:
        c.execute('insert into source_binding(item_id,logical_item_id,logical_article_id,origin) values(?,?,?,?)',
                  (i+'-item',i+'-item',i,'user_original'))
    for number,(i,concept,attr) in enumerate(specs,1):
        c.execute('insert into edge_item_concept(id,item_id,concept,rel) values(?,?,?,?)',
                  (number,i+'-item',concept,'근거'))
        c.execute('insert into edge_attr(edge_id,attr) values(?,?)',(number,attr))
    c.commit();c.close()
    return DomainGraph(db),corpus


def test_every_seed_key_runs_without_switching_at_shared_candidate(tmp_path):
    graph,corpus=fixture_graph(tmp_path)
    ranking,paths=graph.rank(corpus,[corpus['seed']],'',{})
    assert set(ranking)=={'vacation','pay','shared','child'}
    assert {p['domain_key'] for p in paths['shared']}=={'휴가','보수'}
    assert len(ranking)==len(set(ranking))
    # The shared candidate's personnel edge cannot open a new branch.
    assert 'personnel' not in ranking
    assert 'wrong_vacation' not in ranking and 'wrong_pay' not in ranking


def test_all_keys_on_shared_source_are_eligible(tmp_path):
    graph,corpus=fixture_graph(tmp_path)
    ranking,paths=graph.rank(corpus,[corpus['shared']],'',{})
    assert 'personnel' in ranking
    assert {p['domain_key'] for group in paths.values() for p in group}=={'휴가','보수','인사'}


def test_inactive_edges_do_not_open_branches(tmp_path):
    graph,corpus=fixture_graph(tmp_path)
    for e in graph.edges:
        if e['logical_article_id']=='seed' and e['domain_key']=='보수':e['active']=False
    ranking,paths=graph.rank(corpus,[corpus['seed']],'',{})
    assert 'pay' not in ranking
    assert all(p['domain_key']=='휴가' for group in paths.values() for p in group)


def test_real_database_preserves_baseline_candidates_and_adds_branch_provenance():
    corpus={r['id']:r for r in map(json.loads,(HERE/'corpus.jsonl').read_text(encoding='utf8').splitlines())}
    graph=DomainGraph();old=LegacyGraph()
    # Shared annex and a curated original leave article.
    for aid in ['23801:별표','24024:30']:
        ranking,paths=graph.rank(corpus,[corpus[aid]],'',{})
        expected,_=old.rank(corpus,[corpus[aid]],'',{})
        assert ranking==expected
        for group in paths.values():
            for p in group:
                assert graph.domain_keys[p['seed_concept']]==p['domain_key']==graph.domain_keys[p['concept']]
