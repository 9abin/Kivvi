"""Frozen PR #11 Korean bigram BM25; no ML/model import side effects."""
import collections,math,re
import numpy as np

def grams(s, n=2):
    s = re.sub(r"[^0-9A-Za-z가-힣]", "", s)
    return [s[i:i + n] for i in range(len(s) - n + 1)]


class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.tf = [collections.Counter(grams(d)) for d in docs]
        self.len = np.array([sum(t.values()) for t in self.tf], dtype=float)
        self.avg = self.len.mean()
        df = collections.Counter()
        for t in self.tf:
            df.update(t.keys())
        n = len(docs)
        self.idf = {g: math.log(1 + (n - c + 0.5) / (c + 0.5)) for g, c in df.items()}
        self.inv = collections.defaultdict(list)
        for i, t in enumerate(self.tf):
            for g, f in t.items():
                self.inv[g].append((i, f))

    def scores(self, query):
        out = np.zeros(len(self.tf))
        for g in set(grams(query)):
            for i, f in self.inv.get(g, ()):
                out[i] += self.idf[g] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
        return out
