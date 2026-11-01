"""ロト7分析の共通ロジック (標準ライブラリのみ)。"""
from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from math import comb
from pathlib import Path
from statistics import median

N_NUM = 37      # 1〜37
PICK = 7        # 本数字7個
N_BONUS = 2     # ボーナス数字2個
PRICE = 300     # 1口300円
TOTAL_COMBOS = comb(N_NUM, PICK)  # 10,295,472

DATA_PATH = Path(__file__).resolve().parent / "data" / "loto7.csv"


@dataclass
class Draw:
    round: int
    date: str
    nums: tuple[int, ...]
    bonus: tuple[int, int]
    winners: tuple[int, ...]   # 1〜6等の口数
    prizes: tuple[int, ...]    # 1〜6等の1口あたり金額
    carryover: int             # この回の後に繰り越された額


def load_draws(path: Path = DATA_PATH) -> list[Draw]:
    out = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(Draw(
                round=int(r["round"]),
                date=r["date"],
                nums=tuple(int(r[f"n{i}"]) for i in range(1, 8)),
                bonus=(int(r["b1"]), int(r["b2"])),
                winners=tuple(int(r[f"w{k}"]) for k in range(1, 7)),
                prizes=tuple(int(r[f"p{k}"]) for k in range(1, 7)),
                carryover=int(r["carryover"]),
            ))
    out.sort(key=lambda d: d.round)
    return out


# ---------------------------------------------------------------- 等級の確率
def p_match(k: int, j: int) -> float:
    """本数字k個・ボーナスj個が一致する確率 (1口あたり)。"""
    rest = PICK - k - j
    if rest < 0:
        return 0.0
    return comb(PICK, k) * comb(N_BONUS, j) * comb(N_NUM - PICK - N_BONUS, rest) / TOTAL_COMBOS


def tier_probs() -> list[float]:
    """1〜6等の当せん確率。
    1等: 本7 / 2等: 本6+ボ1 / 3等: 本6 / 4等: 本5 / 5等: 本4 / 6等: 本3+ボ1以上
    """
    return [
        p_match(7, 0),
        p_match(6, 1),
        p_match(6, 0),
        sum(p_match(5, j) for j in range(3)),
        sum(p_match(4, j) for j in range(4)),
        p_match(3, 1) + p_match(3, 2),
    ]


TIER_P = tier_probs()


# ---------------------------------------------------------------- 統計関数
def _gammainc_lower_reg(a: float, x: float) -> float:
    """正則化下側不完全ガンマ関数 P(a, x)。"""
    if x <= 0:
        return 0.0
    if x < a + 1:  # 級数展開
        term = s = 1.0 / a
        n = a
        for _ in range(10000):
            n += 1
            term *= x / n
            s += term
            if abs(term) < abs(s) * 1e-15:
                break
        return s * math.exp(-x + a * math.log(x) - math.lgamma(a))
    # 連分数 (Lentz法) で上側を計算
    b = x + 1 - a
    c = 1 / 1e-300
    d = 1 / b
    h = d
    for i in range(1, 10000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1e-300 if abs(d) < 1e-300 else d
        c = b + an / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1 / d
        delta = d * c
        h *= delta
        if abs(delta - 1) < 1e-15:
            break
    return 1 - math.exp(-x + a * math.log(x) - math.lgamma(a)) * h


def chi2_sf(x: float, df: int) -> float:
    """カイ二乗分布の上側確率 (p値)。"""
    return 1 - _gammainc_lower_reg(df / 2, x / 2)


def normal_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2))


# 1回の抽せんで、ある7個の予想と本数字が何個一致するか (超幾何分布)
EXPECTED_HITS = PICK * PICK / N_NUM
VAR_HITS = PICK * (PICK / N_NUM) * (1 - PICK / N_NUM) * (N_NUM - PICK) / (N_NUM - 1)


def hit_dist() -> list[float]:
    return [comb(PICK, k) * comb(N_NUM - PICK, PICK - k) / TOTAL_COMBOS for k in range(PICK + 1)]


def sum_distribution() -> dict[int, float]:
    """7個の合計値の理論分布 (全組合せ数え上げをDPで)。"""
    # dp[c][s] = c個選んで合計sの組合せ数
    max_s = sum(range(N_NUM - PICK + 1, N_NUM + 1))
    dp = [[0] * (max_s + 1) for _ in range(PICK + 1)]
    dp[0][0] = 1
    for x in range(1, N_NUM + 1):
        for c in range(PICK, 0, -1):
            row, prev = dp[c], dp[c - 1]
            for s in range(max_s, x - 1, -1):
                if prev[s - x]:
                    row[s] += prev[s - x]
    return {s: v / TOTAL_COMBOS for s, v in enumerate(dp[PICK]) if v}


def consecutive_pairs(nums) -> int:
    s = sorted(nums)
    return sum(1 for a, b in zip(s, s[1:]) if b - a == 1)


# ---------------------------------------------------------------- 線形代数
def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """ガウスの消去法 (部分ピボット) で a x = b を解く。"""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        p = m[col][col]
        for r in range(n):
            if r != col and m[r][col]:
                f = m[r][col] / p
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] for i in range(n)]


def ridge(xs: list[list[float]], ys: list[float], lam: float, unpenalized: int = 1) -> list[float]:
    """リッジ回帰。先頭 unpenalized 列 (切片など) は罰則なし。"""
    p = len(xs[0])
    xtx = [[0.0] * p for _ in range(p)]
    xty = [0.0] * p
    for x, y in zip(xs, ys):
        for i in range(p):
            xi = x[i]
            if xi == 0:
                continue
            xty[i] += xi * y
            row = xtx[i]
            for j in range(p):
                row[j] += xi * x[j]
    for i in range(unpenalized, p):
        xtx[i][i] += lam
    return solve(xtx, xty)


# ---------------------------------------------------------------- 人気(かぶり)モデル
@dataclass
class PopularityModel:
    """「その回の当せん番号を買っていた人の多さ」から、各数字の人気を推定したもの。

    考え方: 4〜6等の当せん口数は「当せん番号に近い数字を買った人の数」。
    販売口数の長期トレンドを取り除いた残差 y が大きい回は、人気の数字が出た回。
    y を「どの数字が出たか」(37個のダミー変数) と「連番の数」で回帰すると、
    数字ごとの人気度が得られる。係数 > 0 は「選ぶ人が多い」= 当たっても山分けで減る数字。
    """
    num_coef: dict[int, float]     # 数字ごとの人気度 (対数スケール)
    consec_coef: float             # 連番1組あたりの人気度
    carry_coef: float
    oos_corr: float                # 前半で学習→後半(キャリーなしの回)を予測したときの相関
    n_draws: int

    def score(self, nums) -> float:
        return sum(self.num_coef[n] for n in nums) + self.consec_coef * consecutive_pairs(nums)

    def ratio(self, nums) -> float:
        """平均的な組合せと比べて、同じ番号を買っている人が何倍いそうか。"""
        return math.exp(self.score(nums))


def draw_popularity_index(draws: list[Draw], window: int = 10) -> list[tuple[Draw, float, bool]]:
    """各回の「当せん番号の人気度」y と、キャリーオーバー回かどうか。

    推定販売口数 N = (4等+5等+6等の口数) / (4〜6等の確率の和)
    y = log(N / 前後window回のNの中央値)   ※自分自身は除く
    """
    p456 = sum(TIER_P[3:6])
    est = []
    for d in draws:
        w = sum(d.winners[3:6])
        est.append(w / p456 if w > 0 else None)
    out = []
    for i, d in enumerate(draws):
        if est[i] is None:
            continue
        nb = [est[j] for j in range(max(0, i - window), min(len(draws), i + window + 1))
              if j != i and est[j] is not None]
        if len(nb) < window:
            continue
        carry_in = i > 0 and draws[i - 1].carryover > 0
        out.append((d, math.log(est[i] / median(nb)), carry_in))
    return out


def _design(d: Draw, carry: bool) -> list[float]:
    base = PICK / N_NUM
    x = [1.0, 1.0 if carry else 0.0, float(consecutive_pairs(d.nums))]
    s = set(d.nums)
    x += [(1.0 if n in s else 0.0) - base for n in range(1, N_NUM + 1)]
    return x


def fit_popularity(draws: list[Draw], lam: float = 8.0) -> PopularityModel:
    rows = draw_popularity_index(draws)
    xs = [_design(d, c) for d, _, c in rows]
    ys = [y for _, y, _ in rows]
    coef = ridge(xs, ys, lam, unpenalized=3)

    # 時系列で前半学習 → 後半予測 (過学習していないかの確認)
    half = len(rows) // 2
    c_half = ridge(xs[:half], ys[:half], lam, unpenalized=3)
    # キャリーオーバー効果を除き「数字の人気」だけでどれだけ当てられるか (キャリーなしの回で評価)
    test = [i for i in range(half, len(rows)) if not rows[i][2]]
    pred = [sum(a * b for a, b in zip(c_half[2:], xs[i][2:])) for i in test]
    oos = pearson(pred, [ys[i] for i in test])

    return PopularityModel(
        num_coef={n: coef[3 + n - 1] for n in range(1, N_NUM + 1)},
        consec_coef=coef[2],
        carry_coef=coef[1],
        oos_corr=oos,
        n_draws=len(rows),
    )


def pearson(a: list[float], b: list[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    return cov / math.sqrt(va * vb) if va and vb else 0.0


def fmt_small_pct(p: float) -> str:
    """小さい確率でも有効数字3桁が見えるように % 表記する (例: 0.00000971%)。"""
    if p <= 0:
        return "0%"
    digits = max(2, 2 - math.floor(math.log10(p * 100)))
    return f"{p * 100:.{digits}f}%"
