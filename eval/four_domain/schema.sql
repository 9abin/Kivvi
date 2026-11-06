CREATE TABLE article(id TEXT PRIMARY KEY, doc TEXT REFERENCES document(name), label TEXT, title TEXT);

CREATE TABLE change_log(seq INTEGER PRIMARY KEY, concept TEXT, target TEXT, before TEXT, after TEXT, why TEXT, decided_by TEXT);

CREATE TABLE concept(name TEXT PRIMARY KEY, kind TEXT, parent TEXT, rel_to_parent TEXT, note TEXT);

CREATE TABLE concept_attr(concept TEXT REFERENCES concept(name), key TEXT, value TEXT, ord INTEGER, PRIMARY KEY(concept, key));

CREATE TABLE concept_primary(concept TEXT REFERENCES concept(name), article_id TEXT REFERENCES article(id), PRIMARY KEY(concept, article_id));

CREATE TABLE document(name TEXT PRIMARY KEY, ord INTEGER);

CREATE TABLE edge_attr(edge_id INTEGER REFERENCES edge_item_concept(id), attr TEXT, in_concept INTEGER, PRIMARY KEY(edge_id, attr));

CREATE TABLE edge_head(child_item_id TEXT REFERENCES item(id), parent_item_id TEXT REFERENCES item(id), concept TEXT REFERENCES concept(name), PRIMARY KEY(child_item_id, concept));

CREATE TABLE edge_item_concept(id INTEGER PRIMARY KEY, item_id TEXT REFERENCES item(id), concept TEXT REFERENCES concept(name),
  rel TEXT CHECK(rel IN ('정의','근거','언급')), memo TEXT, changed_from TEXT, changed_to TEXT, decided_by TEXT, UNIQUE(item_id, concept));

CREATE TABLE edge_rule_fact(id INTEGER PRIMARY KEY, edge_id INTEGER REFERENCES edge_item_concept(id), attr TEXT, value TEXT, scope TEXT, support_item_id TEXT REFERENCES item(id), quote TEXT, quote_source TEXT);

CREATE TABLE item(id TEXT PRIMARY KEY, article_id TEXT REFERENCES article(id), num TEXT, level TEXT, text TEXT);

CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE review_evidence(edge_id INTEGER PRIMARY KEY REFERENCES edge_item_concept(id), unit_id TEXT, quote TEXT, reason TEXT, temporal_scope TEXT, review_status TEXT, model TEXT, quote_source TEXT);

CREATE TABLE review_provenance(key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE source_binding(item_id TEXT PRIMARY KEY REFERENCES item(id), logical_item_id TEXT, logical_article_id TEXT, unit_id TEXT, origin TEXT, source_text_sha256 TEXT);

CREATE VIEW v_attr_support AS
  SELECT ca.concept, ca.key, ca.value, count(x.edge_id) AS n_support
  FROM concept_attr ca LEFT JOIN edge_item_concept e ON e.concept = ca.concept AND e.rel = '근거'
  LEFT JOIN edge_attr x ON x.edge_id = e.id AND x.attr = ca.key
  GROUP BY ca.concept, ca.key ORDER BY ca.concept, ca.ord;

CREATE VIEW v_candidates AS
  SELECT e1.item_id AS changed_item, e1.concept, x1.attr, e2.item_id AS candidate_item, a2.doc, a2.label, i2.num, i2.text
  FROM edge_item_concept e1 JOIN edge_attr x1 ON x1.edge_id = e1.id
  JOIN edge_attr x2 ON x2.attr = x1.attr JOIN edge_item_concept e2 ON e2.id = x2.edge_id AND e2.concept = e1.concept AND e2.rel = '근거' AND e2.item_id <> e1.item_id
  JOIN item i2 ON i2.id = e2.item_id JOIN article a2 ON a2.id = i2.article_id
  WHERE e1.rel = '근거';

CREATE VIEW v_edge AS
  SELECT e.id, e.concept, a.doc, a.label AS article, i.num, i.id AS item_id, e.rel,
         (SELECT group_concat(attr, '·') FROM edge_attr x WHERE x.edge_id = e.id) AS attrs,
         e.memo, i.text, e.decided_by, e.changed_from, e.changed_to
  FROM edge_item_concept e JOIN item i ON i.id = e.item_id JOIN article a ON a.id = i.article_id;
