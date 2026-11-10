import unittest
from ontology_search.scoped_propagating_graph import ScopedPropagatingGraph
from ontology_search.propagating_graph import walk_events,Limits

class Schema:
    def __init__(self,edges):self.edges=edges
    def related(self,a,b):return a==b

def edge(article,item):
    return dict(active=True,logical_article_id=article,logical_item_id=item,concept='수당',attrs={'금액'},rel='근거')

class ScopedTests(unittest.TestCase):
    def graph(self):
        corpus={
          'fee:1':dict(id='fee:1',reg_id='fee',document='강사료기준지침',label='제1조',text='강사료 지급액은 100만원으로 한다.',items=[dict(logical_id='fee:1-0-0-0',text='강사료 지급액은 100만원으로 한다.')]),
          'edu:6':dict(id='edu:6',reg_id='edu',document='교육훈련지침',label='제6조',text='제6조\n① 교육비는 별도로 정한다.\n② 강사료기준지침에 따라 강사료 또는 원고료를 지급한다.',items=[dict(logical_id='edu:6-1-0-0',text='교육비는 별도로 정한다.')]),
          'other:1':dict(id='other:1',reg_id='other',document='다른지침',label='제1조',text='교육비 지급액은 50만원이다.',items=[dict(logical_id='other:1-0-0-0',text='교육비 지급액은 50만원이다.')]),
        }
        return ScopedPropagatingGraph(corpus,Schema([edge('fee:1','fee:1-0-0-0'),edge('edu:6','edu:6-1-0-0'),edge('other:1','other:1-0-0-0')]),[],lambda s:s,lambda s:''.join(s.split()))

    def test_delegation_does_not_open_unreached_sibling_item(self):
        graph=self.graph();p=graph.initial_payload('fee:1',['fee:1-0-0-0'],{'종류':'값 변경','개정문':'제1조 중 "100만원"을 "150만원"으로 한다.'},'강사료 지급액은 100만원으로 한다.')
        edges=graph.neighbors('fee:1',p,0)
        delegation=next(e for e in edges if e['target']=='edu:6' and e['kind']=='regulation_delegation_in')
        self.assertTrue(delegation['impact'])
        self.assertTrue(all('교육비' not in s for s in delegation['payload']['active_spans']))
        onward=graph.neighbors('edu:6',delegation['payload'],1)
        self.assertFalse(any(e['kind']=='schema_concept_attribute' for e in onward))

    def test_terminal_candidate_never_becomes_second_hop_seed(self):
        def neighbors(n,p,d):
            target={'root':'terminal','terminal':'unrelated'}.get(n)
            return [dict(target=target,kind='reference',weight=1.,impact=False,reason='not enough evidence')] if target else []
        scores,_,_=walk_events('root',{},neighbors,Limits(initial_hops=1))
        self.assertEqual(set(scores),{'terminal'})

    def test_change_property_is_procedure_not_all_salary_context(self):
        graph=self.graph()
        p=graph.initial_payload('fee:1',['fee:1-0-0-0'],{'종류':'고립 수정','개정문':'제1조 중 "대표이사는"을 "대표이사는 인사부서와 협의하여"로 한다.'},'대표이사는 강사료 지급액 방침을 정한다.')
        self.assertEqual(p['root_property_tags'],['procedure'])
        self.assertFalse(graph.fits('강사료 지급액은 100만원이다.',p))

if __name__=='__main__':unittest.main()
