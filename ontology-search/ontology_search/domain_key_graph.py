"""Domain-key branches over shared source nodes, without modifying the frozen DB.

Every seed domain is searched independently. Shared candidate items never add
new seed domains; article results are deduplicated by their best edge score.
The frozen validated_graph adapter remains available for baseline reproduction.
"""
import collections

from .validated_graph import DomainGraph as FrozenDomainGraph


class DomainGraph(FrozenDomainGraph):
    algorithm_version = 'all-seed-domain-keys-v1'

    def __init__(self, path=None):
        super().__init__(path)
        self.domain_keys = {name: self._domain_key(name) for name in self.parents}
        for edge in self.edges:
            edge['domain_key'] = self.domain_keys[edge['concept']]
        for head in self.heads:
            head['domain_key'] = self.domain_keys[head['concept']]
        self.stats.update(algorithm_version=self.algorithm_version,
                          domain_keys=sorted(set(self.domain_keys.values())))

    def _domain_key(self, name):
        seen = set()
        while True:
            if name in seen or name not in self.parents:
                raise ValueError('Invalid concept hierarchy: ' + str(name))
            seen.add(name)
            parent = self.parents[name]
            if not parent:
                return name
            name = parent

    def rank(self, corpus, anchors, query, lex, seed_items=None):
        current = {r.get('logical_id', r['id']): r for r in corpus.values()}
        available_items = {i.get('logical_id', i['id'])
                           for row in corpus.values() for i in row.get('items', [])
                           if not i.get('deleted')}
        edges = [e for e in self.edges
                 if e['active'] and e['logical_article_id'] in current
                 and (not current[e['logical_article_id']].get('items')
                      or e['logical_item_id'] in available_items)]
        anchor_ids = {r.get('logical_id', r['id']) for r in anchors}
        excluded = {r['id'] for r in anchors}
        explicit = set(seed_items or [])
        if not explicit:
            explicit = {e['logical_item_id'] for e in edges
                        if e['logical_article_id'] in anchor_ids}
        linked = collections.defaultdict(set)
        for head in self.heads:
            if head['parent'] in explicit:
                linked[head['concept']].add(head['child'])
            if head['child'] in explicit:
                linked[head['concept']].add(head['parent'])
        seeds = [e for e in edges if e['logical_article_id'] in anchor_ids
                 and (e['logical_item_id'] in explicit
                      or e['logical_item_id'] in linked[e['concept']])]
        if not seeds:
            seeds = [e for e in edges if e['logical_article_id'] in anchor_ids]

        # Keys belong to connections, not the shared article/item as a whole.
        # Every eligible seed key starts a branch; no classifier chooses one.
        branches = collections.defaultdict(lambda: collections.defaultdict(set))
        for seed in seeds:
            branches[seed['domain_key']][seed['concept']].update(seed['attrs'])
        candidates = collections.defaultdict(list)
        for edge in edges:
            candidates[edge['domain_key']].append(edge)

        scores, paths = {}, collections.defaultdict(list)
        for domain_key in sorted(branches):
            for edge in candidates[domain_key]:
                article_id = current[edge['logical_article_id']]['id']
                if article_id in excluded:
                    continue
                best, overlap, seed_concept = 0, set(), None
                for concept, attrs in branches[domain_key].items():
                    relation = self.related(concept, edge['concept'])
                    matching = attrs & edge['attrs']
                    if not relation or (attrs and not matching):
                        continue
                    weight = relation + min(len(matching), 4) * .5 + {
                        '근거': .3, '정의': .2, '언급': .1}[edge['rel']]
                    if weight > best:
                        best, overlap, seed_concept = weight, matching, concept
                if not best:
                    continue
                # Multiple branches cannot inflate a shared article's score.
                scores[article_id] = max(scores.get(article_id, 0), best)
                paths[article_id].append({
                    'domain_key': domain_key,
                    'seed_concept': seed_concept, 'concept': edge['concept'],
                    'attrs': sorted(overlap), 'rel': edge['rel'],
                    'logical_item_id': edge['logical_item_id'],
                    'origin': 'user_original' if edge['original'] else 'ai_source_review',
                })
        ranked = sorted(scores, key=lambda aid: (-scores[aid], -lex.get(aid, 0), aid))
        return ranked, dict(paths)
