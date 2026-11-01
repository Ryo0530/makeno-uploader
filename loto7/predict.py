#!/usr/bin/env python3
"""ロト7 予想システム (監査役つき)。

  1. 次の回の予想を、全予想法で作る
  2. 抽せん前に台帳 (data/ledger.csv) へ記録する (後から書き換えられないようにハッシュチェーンで保存)
  3. 監査役 (auditor.py) が データ・リーク・成績・確率・台帳 を検査し、audit_report.html を作る
  4. 監査済みの買い目と、正確な当せん確率を表示する

使い方:
  python3 update_data.py         # まず最新の当せん結果を取得
  python3 predict.py             # 次回の予想 + 監査 (既定は「人とかぶりにくい」5口)
  python3 predict.py -k 3        # 3口
  python3 predict.py --show all  # 全予想法の買い目を表示
  python3 predict.py --no-record # 台帳に記録しない (お試し)
"""
from __future__ import annotations

import argparse
import random

from auditor import ALPHA, REPORT, append_ledger, read_ledger, run_full_audit
from loto7lib import fmt_small_pct, PRICE, TIER_P, TOTAL_COMBOS, load_draws
from predictors import PREDICTORS

TIER_NAMES = ["1等", "2等", "3等", "4等", "5等", "6等"]
RECOMMENDED = "unpopular"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-k", type=int, default=5, help="1つの予想法あたり何口 (既定: 5)")
    ap.add_argument("--show", default=RECOMMENDED, help=f"表示する予想法 (カンマ区切り / all)。選択肢: {', '.join(PREDICTORS)}")
    ap.add_argument("--no-record", action="store_true", help="台帳に記録しない")
    args = ap.parse_args()

    draws = load_draws()
    target = draws[-1].round + 1
    show = list(PREDICTORS) if args.show == "all" else [s.strip() for s in args.show.split(",")]
    unknown = [s for s in show if s not in PREDICTORS]
    if unknown:
        ap.error(f"不明な予想法: {unknown}")

    # ---- 1〜2. 予想して台帳に記録 (台帳に既にあればそれを使う = 選び直し禁止)
    existing = [r for r in read_ledger() if int(r["target_round"]) == target]
    if existing:
        preds: dict[str, list] = {}
        for r in existing:
            preds.setdefault(r["predictor"], []).append(tuple(int(x) for x in r["ticket"].split()))
        print(f"第{target}回の予想は台帳に記録済みです (記録日時 {existing[0]['created_at']})。記録済みの買い目を使います。")
    else:
        preds = {key: p.predict(draws, args.k, random.Random(f"pred-{key}-{target}")) for key, p in PREDICTORS.items()}
        if args.no_record:
            print("※ 台帳に記録していません (--no-record)")
        else:
            n = append_ledger(target, draws[-1].round, [(k, t) for k, ts in preds.items() for t in ts])
            print(f"第{target}回の予想を台帳に記録しました ({n}口, data/ledger.csv)")

    # ---- 3. 監査
    print("\n監査役がチェック中...")
    tickets = preds[RECOMMENDED]
    data_chk, leak_chk, perf, prob, ledger = run_full_audit(draws, tickets, progress=lambda m: print("  " + m))
    checks_ok = (all(c[1] for c in data_chk + leak_chk) and prob["sim_ok"]
                 and not ledger["broken"] and not ledger["late"])
    res = perf["results"]

    # ---- 4. 表示
    print("\n" + "=" * 64)
    print(f" 第{target}回 ロト7 予想  (データ: 第{draws[-1].round}回 {draws[-1].date} まで)")
    print("=" * 64)
    for key in show:
        p = PREDICTORS[key]
        v = res[key]
        print(f"\n■ {p.name} — {p.desc}")
        for t in preds.get(key, []):
            print("    " + " ".join(f"{x:02d}" for x in t))
        padj = "" if v["p_adj"] is None else f" / 補正後p値 {v['p_adj']:.2f}"
        print(f"  監査: 過去{perf['n_draws']}回の平均一致 {v['mean_hits']:.3f}個{padj} → {v['verdict']}")

    print("\n" + "-" * 64)
    print(" 監査役の報告")
    print("-" * 64)
    print(f"  検査項目: {'すべて合格' if checks_ok else '不合格あり (audit_report.html を確認)'}")
    winners = [k for k, v in res.items() if k != "random" and v["p_adj"] < ALPHA]
    if winners:
        print(f"  バックテストで有意: {', '.join(PREDICTORS[w].name for w in winners)}"
              " (当てはめ過ぎの可能性あり。事前予想の成績で確認するまで保留)")
    else:
        print("  完全ランダムより当たる予想法: なし (8つの予想法すべて、偶然の範囲内)")
    print(f"\n  当せん確率 (1口あたり、どの予想法でも同じ。模擬抽せん{prob['sims']:,}回で検証済み):")
    for name, p in zip(TIER_NAMES, TIER_P):
        print(f"    {name}: 1/{1/p:>12,.0f}  ({fmt_small_pct(p)})")
    k = len(tickets)
    print(f"\n  {k}口買った場合 ({k*PRICE:,}円):")
    print(f"    どれかが1等     : 1/{1/prob['p_first_k']:,.0f}")
    print(f"    どれかが6等以上 : {prob['p_any_k_sim']*100:.1f}%")
    print(f"    期待値の目安    : {prob['ev_per_ticket']*k:,.0f}円 (払った額の {prob['ev_per_ticket']/PRICE*100:.0f}%)")
    print(f"\n詳しい監査レポート: {REPORT}")


if __name__ == "__main__":
    main()
