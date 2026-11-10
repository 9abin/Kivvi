import unittest
from ontology_search.propagating_graph import walk_events,Limits,PropagatingGraph

class PropagationTests(unittest.TestCase):
    def edge(self,target,impact=True,payload=None):
        e=dict(target=target,kind='test',weight=1.0,impact=impact,reason='fixture')
        if payload is not None:e['payload']=payload
        return e

    def test_cycles_and_duplicate_paths_terminate(self):
        adj={'A':[self.edge('B'),self.edge('C')], 'B':[self.edge('C'),self.edge('A')], 'C':[self.edge('B')]}
        scores,paths,stats=walk_events('A',{'delta':'x'},lambda n,p,d:adj.get(n,[]))
        self.assertEqual(set(scores),{'B','C'})
        self.assertEqual(stats['processed'],3)
        self.assertGreater(stats['duplicate_event_skips'],0)

    def test_gate_stops_after_initial_two_hops(self):
        adj={'A':[self.edge('B')], 'B':[self.edge('C',False)], 'C':[self.edge('D')]}
        scores,_,_=walk_events('A',{'delta':'x'},lambda n,p,d:adj.get(n,[]))
        self.assertEqual(set(scores),{'B','C'})
        adj['B'][0]['impact']=True
        scores,_,stats=walk_events('A',{'delta':'x'},lambda n,p,d:adj.get(n,[]))
        self.assertIn('D',scores)
        self.assertGreater(stats['extra_hop_expansions'],0)

    def test_new_fact_can_revisit_same_node_but_state_budget_holds(self):
        def neighbors(n,p,d):
            if n=='A':return [self.edge('B',payload={'delta':1})]
            target='C' if n=='B' else 'B'
            return [self.edge(target,payload={'delta':p['delta']+1})]
        _,_,stats=walk_events('A',{'delta':0},neighbors,Limits(max_depth=30,max_states_per_node=2))
        self.assertEqual(stats['processed'],5)
        self.assertGreater(stats['node_state_stops'],0)

    def test_event_budget_is_hard_bound(self):
        _,_,stats=walk_events('A',{},lambda n,p,d:[self.edge(n+str(i)) for i in range(10)],Limits(max_events=5,max_depth=9))
        self.assertEqual(stats['processed'],5)
        self.assertGreater(stats['event_budget_stops'],0)

    def test_candidate_budget_is_also_hard_bound(self):
        scores,_,stats=walk_events('A',{},lambda n,p,d:[self.edge(n+str(i)) for i in range(10)],Limits(max_nodes=7))
        self.assertLessEqual(len(scores),6)
        self.assertLessEqual(stats['visited_nodes'],7)

    def test_definition_does_not_leak_to_another_regulation(self):
        class Schema:
            edges=[]
            def related(self,a,b):return a==b
        corpus={i:dict(id=i,reg_id=rid,document=rid,label='제1조',text=text,items=[])
                for i,rid,text in [('root','r1','임원 정의'),('same','r1','임원의 급여'),('other','r2','임원의 급여')]}
        graph=PropagatingGraph(corpus,Schema(),[],lambda s:s,lambda s:s.replace(' ',''))
        payload=dict(kind='정의 변경',root_reg='r1',scope_terms=[],definition_terms=['임원'],concepts={},focus_text='임원 정의')
        edges=graph.neighbors('root',payload,0)
        definitions={e['target'] for e in edges if e['kind']=='definition_use'}
        self.assertEqual(definitions,{'same'})

    def test_wording_change_still_follows_explicit_reference(self):
        from ontology_search.scoped_propagating_graph import ScopedPropagatingGraph
        class Schema:
            edges=[]
            def related(self,a,b):return a==b
        corpus={
            'r1:1':dict(id='r1:1',reg_id='r1',document='규정A',label='제1조',text='공개해야 한다.',items=[]),
            'r2:1':dict(id='r2:1',reg_id='r2',document='규정B',label='제1조',text='규정A 제1조에 따라 공개한다.',items=[]),
        }
        chunks=[{'고유번호':'r2','조항':'1','cites':[{'유형':'내부','대상':'r1_1','원문':'규정A 제1조'}]}]
        for cls in [PropagatingGraph,ScopedPropagatingGraph]:
            with self.subTest(graph=cls.__name__):
                graph=cls(corpus,Schema(),chunks,lambda s:s,lambda s:''.join(s.split()))
                payload=graph.initial_payload('r1:1',[],{'종류':'표현 수정','개정문':'제1조 중 "공개해야 한다"를 "공개하여야 한다"로 한다.'},'공개해야 한다.')
                ranked,_,stats=graph.rank('r1:1',payload,{})
                self.assertIn('r2:1',ranked)
                self.assertGreater(stats['edges_examined'],0)

if __name__=='__main__':unittest.main()
