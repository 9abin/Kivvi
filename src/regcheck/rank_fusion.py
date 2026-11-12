"""Rank fusion shared by the four-domain evaluation and article review."""

from collections import defaultdict


def fuse(*rankings: list[str]) -> list[str]:
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, article_id in enumerate(ranking[:100], 1):
            scores[article_id] += 1 / (60 + rank)
    return sorted(scores, key=lambda article_id: (-scores[article_id], article_id))
