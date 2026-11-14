"""Paragraph-scoped, terminal-candidate-aware provisional impact propagation.

No semantic confirmation is claimed: scope guards are deterministic heuristics.
Source text is held in memory; neither ontology DB nor frozen fixtures change.
"""
import collections,re
from .propagating_graph import PropagatingGraph,Limits,terms,walk_events

PROPERTY_PATTERNS={
 'payment':r'보수|급여|수당|강사료|원고료|연봉|퇴직금|지급액|금액|평균임금',
 'duration':r'기간|기한|개월|연간|이내|이상|일수|\d+\s*(?:년|일|회)',
 'procedure':r'승인|협의|결재|제출|신청|보고|허가|통보',
 'evaluation':r'평정|평가|가점|평가자|평가대상',
 'vote':r'의결|찬성|정족수',
 'storage':r'보관|보존|편철|기록물|문서철',
 'reference':r'따르|따라|의거|준용|법령|규정|지침',
}
# Exclude broad administrative words as sole topic-matching evidence.
GENERIC={'지급','지급할','대상','사용','적용','계산','경우','사항','내용','필요','담당','대표이사','부서장','담당부서장','직원','임원','보수','기준','교육','기간','결정','실시','운영','관리','직급','범위','여부','위하여'}
def properties(text):return {key for key,pattern in PROPERTY_PATTERNS.items() if re.search(pattern,text)}

class ScopedPropagatingGraph(PropagatingGraph):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.units={};self.unit_stats=collections.Counter()
        for aid,row in self.corpus.items():
            text=re.sub(r'(?<!\n)([①-⑳])',r'\n\1',row['text'])
            parts=re.split(r'\n(?=[①-⑳]|\d+\.\s)',text)
            # Raw branch text supplies units even where the DB has no item rows.
            self.units[aid]=list(dict.fromkeys(p.strip() for p in parts if p.strip()))

    def initial_payload(self,anchor,seed_items,amendment,focus_text):
        payload=super().initial_payload(anchor,seed_items,amendment,focus_text)
        topic=terms(focus_text+' '+amendment['개정문'])-GENERIC
        # Root source text often contains document names, which must not act as
        # sole evidence for a downstream subject/attribute match.
        for row in self.corpus.values():topic.discard(self.norm(row['document']))
        changed=re.search(r'중 "(.*)"(?:을|를) "(.*)"(?:으로|로) 한다',amendment['개정문'],re.S)
        changed_props=set()
        if changed:
            changed_text=changed[1]+' '+changed[2]
            changed_props=properties(changed_text)
            if re.search(r'\d.*(?:원|만원)|US\$',changed_text):changed_props.add('payment')
            # Reference words occur in nearly every policy context, so prefer
            # more specific changed attributes when present.
            if changed_props-{'reference'}:changed_props.discard('reference')
        payload.update(active_spans=[focus_text],root_topic_terms=sorted(topic),
                       root_property_tags=sorted(changed_props or properties(focus_text+' '+amendment['개정문'])))
        return payload

    def fits(self,text,payload):
        topic=set(payload['root_topic_terms']);overlap=topic & terms(text)
        root_props=set(payload['root_property_tags']);target_props=properties(text)
        # Same payment/evaluation subject can use different surface nouns, but
        # administrative approval/period wording alone cannot open a branch.
        semantic_family=bool(root_props & target_props & {'payment','evaluation','storage','vote'})
        topic_ok=bool(overlap) or semantic_family
        prop_ok=not root_props or bool(root_props & target_props)
        return topic_ok and prop_ok

    def source_item_in_scope(self,node,edge,spans):
        item=next((it for it in self.corpus[node]['items'] if it['logical_id']==edge['logical_item_id']),None)
        if not item or not item['text'].strip():return False
        return any(self.norm(item['text']) in self.norm(s) or self.norm(s) in self.norm(item['text']) for s in spans if s.strip())

    def neighbors(self,node,payload,depth):
        candidates=super().neighbors(node,payload,depth)
        active=payload.get('active_spans',[]);active_text='\n'.join(active)
        active_compact=self.norm(active_text);result=[]
        for e in candidates:
            target=e['target'];kind=e['kind'];units=self.units[target]
            exact=[];matched=[]
            if kind=='specific_reference_in':
                exact=[u for u in units if self.norm(e['evidence']) in self.norm(u)]
                matched=[u for u in exact if self.fits(u,payload)]
            elif kind=='specific_reference_out':
                if self.norm(e['evidence']) not in active_compact:
                    self.unit_stats['inactive_source_reference_blocked']+=1;continue
                matched=[u for u in units if self.fits(u,payload)]
            elif kind=='regulation_delegation_in':
                # A reference to an entire intermediate document does not mean
                # it depends on the particular affected sentence in that doc.
                if depth>0:
                    self.unit_stats['intermediate_document_fanout_blocked']+=1;continue
                name=self.norm(self.corpus[node]['document'])
                exact=[u for u in units if name in self.norm(u)]
                matched=[u for u in exact if self.fits(u,payload)]
            elif kind=='regulation_delegation_out':
                name=self.norm(self.corpus[target]['document'])
                if name not in active_compact:
                    self.unit_stats['inactive_source_delegation_blocked']+=1;continue
                matched=[u for u in units if self.fits(u,payload)]
            elif kind=='definition_use':
                exact=[u for u in units if self.norm(e['evidence']) in self.norm(u)]
                matched=exact
            elif kind=='schema_concept_attribute':
                source_edges=[se for se in self.article_edges.get(node,[]) if self.source_item_in_scope(node,se,active)]
                concepts=e['payload']['concepts']
                if not any(self.schema.related(concept,se['concept']) and (not attrs or set(attrs)&se['attrs'])
                           for concept,attrs in concepts.items() for se in source_edges):
                    self.unit_stats['unrelated_source_item_schema_blocked']+=1;continue
                for te in self.article_edges.get(target,[]):
                    if not any(self.schema.related(concept,te['concept']) and (not attrs or set(attrs)&te['attrs'])
                               for concept,attrs in concepts.items()):continue
                    it=next((it for it in self.corpus[target]['items'] if it['logical_id']==te['logical_item_id']),None)
                    if it:
                        exact.append(it['text'])
                        if self.fits(it['text'],payload):matched.append(it['text'])
            # Keep a directly evidenced candidate as a terminal result, even if
            # its onward effect is not supported. Mere lexical/schema peers
            # without matching subject/attribute are not useful candidates.
            if kind=='schema_concept_attribute' and not matched:
                self.unit_stats['weak_schema_candidate_filtered']+=1;continue
            if kind.startswith('regulation_delegation') and not matched:
                self.unit_stats['weak_delegation_candidate_filtered']+=1;continue
            matched=list(dict.fromkeys(matched))
            can_expand=bool(matched) and (e['impact'] or kind in {'specific_reference_in','specific_reference_out'})
            narrowed={**e['payload'],'active_spans':matched or list(dict.fromkeys(exact))}
            if not narrowed['active_spans']:narrowed['active_spans']=[]
            result.append({**e,'impact':can_expand,'payload':narrowed,
                'reason':e['reason']+' | 문장 범위 일치: '+str(bool(matched))+'; 후보 표시와 추가 확장은 별도.',
                'weight':e['weight'] if can_expand else e['weight']*.65})
            self.unit_stats['expandable_candidate_edges' if can_expand else 'terminal_candidate_edges']+=1
        return result

    def rank(self,anchor,payload,lexical,limits=Limits()):
        # Gate from the first returned candidate: even within the initial two
        # hops, an unconfirmed candidate cannot silently become a new seed.
        limits=Limits(**{**limits.__dict__,'initial_hops':1})
        before=self.unit_stats.copy()
        scores,paths,stats=walk_events(anchor,payload,self.neighbors,limits)
        stats.update({k:self.unit_stats[k]-before[k] for k in self.unit_stats})
        stats['gating_from_candidate_depth']=1
        return sorted(scores,key=lambda aid:(-scores[aid],-lexical.get(aid,0),aid)),paths,stats
