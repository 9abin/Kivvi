"""Searcher.fuse()의 가중치 인자가 실제로 결합 순서에 반영되는지 확인한다.

모델·DB 없이 fuse()는 self를 쓰지 않으므로 빈 인스턴스로 바로 호출할 수 있다.
가중치 스윕(src/eval/sweep_weights.py)이 이 인자에 기대고 있어서, 기본값이
config.py 값과 같고 override가 실제로 순위를 바꾸는지 고정해 둔다.
"""
import unittest

from config import HYBRID_KEYWORD_WEIGHT, HYBRID_VECTOR_WEIGHT
from src.search.search import Searcher


def bare():
    return Searcher.__new__(Searcher)


class FuseWeightTests(unittest.TestCase):
    def test_default_matches_config_values(self):
        vec = [{"chunk_id": "a"}, {"chunk_id": "b"}]
        key = [{"chunk_id": "b"}, {"chunk_id": "a"}]
        default = [h["chunk_id"] for h in bare().fuse(vec, key)]
        explicit = [h["chunk_id"] for h in
                   bare().fuse(vec, key, vector_weight=HYBRID_VECTOR_WEIGHT, keyword_weight=HYBRID_KEYWORD_WEIGHT)]
        self.assertEqual(default, explicit)

    def test_vector_only_weight_follows_vector_order(self):
        vec = [{"chunk_id": "a"}, {"chunk_id": "b"}]
        key = [{"chunk_id": "b"}, {"chunk_id": "a"}]
        out = bare().fuse(vec, key, vector_weight=1.0, keyword_weight=0.0)
        self.assertEqual([h["chunk_id"] for h in out], ["a", "b"])

    def test_keyword_only_weight_follows_keyword_order(self):
        vec = [{"chunk_id": "a"}, {"chunk_id": "b"}]
        key = [{"chunk_id": "b"}, {"chunk_id": "a"}]
        out = bare().fuse(vec, key, vector_weight=0.0, keyword_weight=1.0)
        self.assertEqual([h["chunk_id"] for h in out], ["b", "a"])


if __name__ == "__main__":
    unittest.main()
