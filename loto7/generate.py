#!/usr/bin/env python3
"""ロト7の買い目ジェネレーター (「人とかぶりにくい」組合せを選ぶ)。

※ どの組合せでも1等の確率は 1/10,295,472 で変わりません。
  このツールが狙うのは「当たったときに山分けされにくい」= 受取額の期待値を上げることです。

使い方:
  python3 generate.py              # 5口
  python3 generate.py -n 10        # 10口
  python3 generate.py --seed 42    # 再現できる乱数
  python3 generate.py --mode random  # 比較用: 完全ランダム (クイックピック相当)
"""
from __future__ import annotations

import argparse
import random

from loto7lib import N_NUM, PICK, PopularityModel, consecutive_pairs, fit_popularity, load_draws

# 人がよく選ぶ「見た目のパターン」を除外するルール
MAX_CONSEC_PAIRS = 1      # 連番は1組まで (例: 12-13 はOK、12-13-14 や 3-4 + 20-21 はNG)
MAX_OVERLAP = 2           # 複数口買うとき、口同士で共通する数字は2個まで (分散させる)
POOL = 200_000            # ランダムに作る候補数
TOP_FRACTION = 0.02       # 人気度が低い上位2%から選ぶ (同じ答えばかりにならないように)


def _pattern_ok(c: tuple[int, ...], past: set[tuple[int, ...]]) -> bool:
    if c in past:                                   # 過去の当せん番号そのまま
        return False
    if consecutive_pairs(c) > MAX_CONSEC_PAIRS:
        return False
    if max(c) <= 31:                                # 全部「誕生日の数字」(1〜31)
        return False
    if len({n % 10 for n in c}) <= 3:               # 下一桁が偏りすぎ (例: 7,17,27,37...)
        return False
    if len({(n - 1) // 7 for n in c}) <= 2:         # マークシートの2行以内に固まる
        return False
    diffs = {b - a for a, b in zip(c, c[1:])}
    if len(diffs) == 1:                             # 等間隔 (例: 5,10,15,...)
        return False
    return True


def generate(n: int, model: PopularityModel, past: set, rng: random.Random, mode: str = "unpopular"):
    if mode == "random":
        return [tuple(sorted(rng.sample(range(1, N_NUM + 1), PICK))) for _ in range(n)]

    cands = set()
    while len(cands) < POOL:
        c = tuple(sorted(rng.sample(range(1, N_NUM + 1), PICK)))
        if _pattern_ok(c, past):
            cands.add(c)
    ranked = sorted(cands, key=model.score)
    top = ranked[: max(n * 50, int(len(ranked) * TOP_FRACTION))]
    rng.shuffle(top)

    picked: list[tuple[int, ...]] = []
    for c in top:
        if all(len(set(c) & set(p)) <= MAX_OVERLAP for p in picked):
            picked.append(c)
            if len(picked) == n:
                break
    return picked


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-n", type=int, default=5, help="何口作るか (既定: 5)")
    ap.add_argument("--seed", type=int, default=None, help="乱数シード")
    ap.add_argument("--mode", choices=["unpopular", "random"], default="unpopular")
    args = ap.parse_args()

    draws = load_draws()
    model = fit_popularity(draws)
    past = {tuple(sorted(d.nums)) for d in draws}
    rng = random.Random(args.seed)
    tickets = generate(args.n, model, past, rng, args.mode)

    print(f"データ: 第1回〜第{draws[-1].round}回 ({draws[-1].date})")
    print(f"モード: {'人とかぶりにくい組合せ' if args.mode == 'unpopular' else '完全ランダム'}\n")
    for i, t in enumerate(tickets, 1):
        nums = " ".join(f"{x:02d}" for x in t)
        print(f"  {i:>2}口目: {nums}   推定人気度 ×{model.ratio(t):.2f}")
    print(
        "\n推定人気度 = 平均的な組合せと比べて、近い番号を買っている人が何倍いそうか。"
        "\n1.00未満なら「人とかぶりにくい」。1等の確率はどの組合せも 1/10,295,472 で同じです。"
    )


if __name__ == "__main__":
    main()
