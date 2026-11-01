"""ロト7 予想ロジック集。

すべての予想法は同じ形をしている:

    predict(history, k, rng) -> list[tuple[int, ...]]   # k口分の買い目 (各7個, 昇順)

history には「予想したい回より前の当せんデータ」だけが渡される。
監査役 (auditor.py) はこの約束が守られているかも検査する。

多くの予想法は「数字ごとの重み」を計算し、その重みで7個を抽出する。
重みが一様なら完全ランダム (クイックピック) と同じになる。
"""
from __future__ import annotations

import math
import random
from collections import Counter
from typing import Callable

from loto7lib import N_NUM, PICK, Draw, fit_popularity

Ticket = tuple[int, ...]
NUMS = range(1, N_NUM + 1)


# ---------------------------------------------------------------- 共通部品
def sample_weighted(weights: dict[int, float], rng: random.Random) -> Ticket:
    """重み付きで7個を非復元抽出 (Efraimidis–Spirakis法)。"""
    keys = {n: rng.random() ** (1.0 / max(w, 1e-12)) for n, w in weights.items()}
    return tuple(sorted(sorted(keys, key=keys.get, reverse=True)[:PICK]))


def tickets_from_weights(weights: dict[int, float], k: int, rng: random.Random) -> list[Ticket]:
    out: list[Ticket] = []
    seen = set()
    for _ in range(k * 20):
        t = sample_weighted(weights, rng)
        if t not in seen:
            seen.add(t)
            out.append(t)
            if len(out) == k:
                break
    return out


def sharpen(score: dict[int, float], beta: float = 1.0) -> dict[int, float]:
    """スコアを標準化して exp(beta*z) の重みにする (beta が大きいほど上位に集中)。"""
    vals = list(score.values())
    mu = sum(vals) / len(vals)
    sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) or 1.0
    return {n: math.exp(beta * (v - mu) / sd) for n, v in score.items()}


# ---------------------------------------------------------------- 各予想法の「数字ごとのスコア」
def score_hot(history: list[Draw], window: int = 50) -> dict[int, float]:
    c = Counter(n for d in history[-window:] for n in d.nums)
    return {n: float(c[n]) for n in NUMS}


def score_cold(history: list[Draw], window: int = 50) -> dict[int, float]:
    return {n: -v for n, v in score_hot(history, window).items()}


def score_overdue(history: list[Draw]) -> dict[int, float]:
    """最後に出てから何回たったか。"""
    gap = {n: len(history) for n in NUMS}
    for i, d in enumerate(history):
        for n in d.nums:
            gap[n] = len(history) - 1 - i
    return {n: float(g) for n, g in gap.items()}


def score_bayes(history: list[Draw]) -> dict[int, float]:
    """全期間の出現率のベイズ推定 (一様な事前分布で縮小)。"""
    c = Counter(n for d in history for n in d.nums)
    prior = 50.0
    return {n: (c[n] + prior) / (len(history) * PICK + prior * N_NUM) for n in NUMS}


def score_markov(history: list[Draw]) -> dict[int, float]:
    """「前回 m が出た次の回に n が出た」頻度から、次回の出やすさを推定。"""
    trans = [[0] * (N_NUM + 1) for _ in range(N_NUM + 1)]
    base = Counter()
    for a, b in zip(history, history[1:]):
        for n in b.nums:
            base[n] += 1
            for m in a.nums:
                trans[m][n] += 1
    last = history[-1].nums
    out = {}
    for n in NUMS:
        num = sum(trans[m][n] for m in last) + 1
        den = sum(sum(trans[m]) for m in last) + N_NUM
        out[n] = num / den
    return out


def score_pair(history: list[Draw], window: int = 200) -> dict[int, float]:
    """前回の当せん番号と「同じ回に出やすい」数字 (共起) を重視。"""
    co = Counter()
    for d in history[-window:]:
        s = d.nums
        for a in s:
            for b in s:
                if a != b:
                    co[(a, b)] += 1
    last = history[-1].nums
    return {n: float(sum(co[(m, n)] for m in last)) for n in NUMS}


SCORERS: dict[str, Callable[[list[Draw]], dict[int, float]]] = {
    "hot": score_hot,
    "cold": score_cold,
    "overdue": score_overdue,
    "bayes": score_bayes,
    "markov": score_markov,
    "pair": score_pair,
}


def score_ensemble(history: list[Draw]) -> dict[int, float]:
    """複数の予想法のスコア (標準化後) の平均。"""
    total = {n: 0.0 for n in NUMS}
    for name in ("hot", "overdue", "bayes", "markov", "pair"):
        s = SCORERS[name](history)
        vals = list(s.values())
        mu = sum(vals) / len(vals)
        sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / len(vals)) or 1.0
        for n in NUMS:
            total[n] += (s[n] - mu) / sd
    return total


# ---------------------------------------------------------------- 予想法の登録
class Predictor:
    def __init__(self, key: str, name: str, desc: str, fn):
        self.key, self.name, self.desc, self._fn = key, name, desc, fn

    def predict(self, history: list[Draw], k: int, rng: random.Random) -> list[Ticket]:
        return self._fn(history, k, rng)


def _weighted(scorer, beta=1.5):
    return lambda h, k, rng: tickets_from_weights(sharpen(scorer(h), beta), k, rng)


def _random(h, k, rng):
    return tickets_from_weights({n: 1.0 for n in NUMS}, k, rng)


class _Unpopular:
    """人とかぶりにくい組合せ。人気モデルは50回ごとに作り直す (毎回だと遅いため)。"""

    def __init__(self):
        self._cache: dict[int, object] = {}

    def __call__(self, h, k, rng, pool=5000):
        from generate import generate  # 循環importを避ける
        key = len(h) // 50 * 50
        if key not in self._cache:
            self._cache[key] = fit_popularity(h[:key] if key >= 120 else h)
        past = {tuple(sorted(d.nums)) for d in h}
        return generate(k, self._cache[key], past, rng, pool=pool)


PREDICTORS: dict[str, Predictor] = {p.key: p for p in [
    Predictor("random", "完全ランダム (基準)", "クイックピックと同じ。他の予想法はこれに勝てるかで評価される", _random),
    Predictor("hot", "ホット数字", "直近50回でよく出ている数字を重視", _weighted(score_hot)),
    Predictor("cold", "コールド数字", "直近50回であまり出ていない数字を重視", _weighted(score_cold)),
    Predictor("overdue", "出遅れ数字", "最後に出てから時間がたっている数字を重視", _weighted(score_overdue)),
    Predictor("bayes", "ベイズ出現率", "全期間の出現率をベイズ推定", _weighted(score_bayes, beta=1.0)),
    Predictor("markov", "マルコフ連鎖", "前回の番号の次の回に出やすかった数字を重視", _weighted(score_markov)),
    Predictor("pair", "ペア共起", "前回の番号と同じ回に出やすい数字を重視", _weighted(score_pair)),
    Predictor("ensemble", "アンサンブル", "上の5つのスコアを平均", _weighted(score_ensemble)),
    Predictor("unpopular", "人とかぶりにくい", "当たる確率は同じ。当たったときの山分けを減らす", _Unpopular()),
]}
