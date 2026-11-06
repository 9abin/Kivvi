import hashlib,importlib.util,json,sqlite3
from pathlib import Path
HERE=Path(__file__).resolve().parent
def connect(name):
    c=sqlite3.connect((HERE/name).resolve().as_uri()+'?mode=ro',uri=True); c.row_factory=sqlite3.Row; return c
def jl(p): return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines() if s.strip()]

def test_every_user_original_row_is_preserved():
    old=connect('hyujik-user-original.db'); new=connect('domain_ontology.reviewed.db')
    for (t,) in old.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'"):
        original={tuple(r) for r in old.execute('select * from '+t)}
        final={tuple(r) for r in new.execute('select * from '+t)}
        assert original<=final,t
    assert old.execute('select count(*) from edge_item_concept').fetchone()[0]==151
    assert old.execute('select count(*) from edge_head').fetchone()[0]==38
    assert old.execute('select count(*) from concept_attr').fetchone()[0]==38
    old.close(); new.close()

def test_user_file_hash_unchanged_and_matches_snapshot():
    source=HERE/'hyujik-user-original.db'
    expected=json.loads((HERE/'graph-validation-summary.json').read_text(encoding='utf-8'))['user_original_sha256']
    assert hashlib.sha256(source.read_bytes()).hexdigest()==expected
    assert hashlib.sha256((HERE/'hyujik-user-original.db').read_bytes()).hexdigest()==expected

def test_database_integrity_and_all_evidence_exact():
    c=connect('domain_ontology.reviewed.db'); units={u['unit_id']:u for u in jl(HERE/'source-units.jsonl')}
    assert c.execute('pragma integrity_check').fetchone()[0]=='ok'
    assert not c.execute('pragma foreign_key_check').fetchall()
    for e in c.execute('select * from review_evidence'):
        u=units[e['unit_id']]; source=u['title'] if e['quote_source']=='article_title' else u['text']
        assert e['quote'] in source
    for e in c.execute('select f.*,b.unit_id from edge_rule_fact f join source_binding b on b.item_id=f.support_item_id'):
        u=units[e['unit_id']]; source=u['title'] if e['quote_source']=='article_title' else u['text']
        assert e['quote'] in source
    c.close()

def attrs(logical,concept):
    c=connect('domain_ontology.reviewed.db')
    got={r[0] for r in c.execute('''select a.attr from edge_attr a join edge_item_concept e on e.id=a.edge_id join source_binding b on b.item_id=e.item_id where b.logical_item_id=? and e.concept=? and b.origin='ai_source_review' ''',(logical,concept))}
    c.close(); return got

def test_committee_quorum_is_not_a_payment_ratio():
    assert '지급률' not in attrs('24024:12-2-0-0','인사위원회')

def test_special_grade_adjustment_count_is_not_split_usage():
    assert '분할' not in attrs('23411:11-2-0-0','인사위원회')

def test_named_relatives_definition_is_not_recruitment_definition():
    c=connect('domain_ontology.reviewed.db')
    rels={r[0] for r in c.execute('''select e.rel from edge_item_concept e join source_binding b on b.item_id=e.item_id where b.logical_item_id='10770:3의1-1-0-0' and e.concept='채용' and b.origin='ai_source_review' ''')}
    assert rels and '정의' not in rels; c.close()

def test_vacation_common_rules_and_pay_definition_are_present():
    assert '계속근로기간' in attrs('23970:20-3-0-0','휴가')
    assert '절차' in attrs('23970:20-2-0-0','휴가')
    c=connect('domain_ontology.reviewed.db')
    assert c.execute('''select count(*) from edge_item_concept e join source_binding b on b.item_id=e.item_id where b.logical_item_id='24047:3-0-1-0' and e.concept='보수' and e.rel='정의' ''').fetchone()[0]>=1
    c.close()

def test_adapter_reads_actual_curated_edges_and_heads():
    sp=importlib.util.spec_from_file_location('validated',HERE/'validated_graph.py'); m=importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    graph=m.DomainGraph(); assert graph.stats['user_original_edges']==151
    assert sum(e['concept']=='휴직' and e['original'] for e in graph.edges)==64
    assert graph.stats['all_head_bindings']>=38
    base={r['id']:r for r in jl(HERE/'base_corpus.jsonl')}
    ranked,paths=graph.rank(base,[base['24024:30']],'육아휴직 자녀 연령',{},['24024:30-1-0-0'])
    assert '24010:43' in ranked
    assert any(p['origin']=='user_original' for p in paths['24010:43'])

def test_prorating_and_interim_settlement_not_split_usage():
    assert '분할' not in attrs('24047:13-0-0-0','보수')
    assert '분할' not in attrs('24125:부칙4:2-0-0-0','퇴직금')
