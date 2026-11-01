#!/usr/bin/env python3
"""ロト7の過去当せんデータを取得・照合して data/loto7.csv に保存する。

公式サイト(宝くじ公式/みずほ銀行)は自動取得を想定していないため、
GitHub上で公開されている2つの独立したデータセットを取得し、
本数字・ボーナス数字が全回で一致することを確認してから保存する。

  主ソース : Joker7822/LOTO7_AI_AGENT  loto7.csv (当せん口数・金額・キャリーオーバー付き)
  照合ソース: ogachan111/loto7          data.json (本数字・ボーナス数字のみ)

使い方:  python3 update_data.py
外部ライブラリ不要 (Python 3.9+ 標準ライブラリのみ)。
"""
from __future__ import annotations

import csv
import io
import json
import sys
import urllib.request
from pathlib import Path

PRIMARY_URL = "https://raw.githubusercontent.com/Joker7822/LOTO7_AI_AGENT/main/loto7.csv"
VERIFY_URLS = [
    "https://raw.githubusercontent.com/ogachan111/loto7/main/data.json",
    "https://raw.githubusercontent.com/ogachan111/loto7/main1/data.json",
]
OUT = Path(__file__).resolve().parent / "data" / "loto7.csv"

FIELDS = (
    ["round", "date"]
    + [f"n{i}" for i in range(1, 8)]
    + ["b1", "b2"]
    + [f"w{k}" for k in range(1, 7)]
    + [f"p{k}" for k in range(1, 7)]
    + ["carryover"]
)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "loto7-analysis/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8-sig")


def yen(s: str) -> int:
    s = s.strip()
    if s in ("", "該当なし", "-"):
        return 0
    return int(s.replace(",", "").replace("円", "").replace("口", ""))


def parse_primary(text: str) -> dict[int, dict]:
    rows = {}
    for r in csv.DictReader(io.StringIO(text)):
        rnd = int(r["回別"].strip().lstrip("第").rstrip("回"))
        nums = sorted(int(x) for x in r["本数字"].split())
        bonus = sorted(int(x) for x in r["ボーナス数字"].split())
        if len(nums) != 7 or len(bonus) != 2 or len(set(nums + bonus)) != 9:
            raise ValueError(f"第{rnd}回: 数字が不正 {nums} {bonus}")
        if not all(1 <= x <= 37 for x in nums + bonus):
            raise ValueError(f"第{rnd}回: 範囲外の数字 {nums} {bonus}")
        row = {"round": rnd, "date": r["抽せん日"].strip()}
        row.update({f"n{i+1}": v for i, v in enumerate(nums)})
        row.update({"b1": bonus[0], "b2": bonus[1]})
        for k in range(1, 7):
            row[f"w{k}"] = yen(r[f"{k}等口数"])
            row[f"p{k}"] = yen(r[f"{k}等当選金額"])
        row["carryover"] = yen(r["キャリーオーバー"])
        rows[rnd] = row
    return rows


def main() -> int:
    print(f"取得中: {PRIMARY_URL}")
    rows = parse_primary(fetch(PRIMARY_URL))

    verify = None
    for url in VERIFY_URLS:
        try:
            print(f"照合用データ取得中: {url}")
            verify = json.loads(fetch(url))
            break
        except Exception as e:  # noqa: BLE001
            print(f"  取得失敗: {e}")
    if verify is None:
        print("照合用データが取れなかったため中止します。", file=sys.stderr)
        return 1

    vmap = {d["round"]: (sorted(d["numbers"]), sorted(d["bonus"])) for d in verify}
    mismatches = []
    for rnd, row in rows.items():
        mine = ([row[f"n{i}"] for i in range(1, 8)], [row["b1"], row["b2"]])
        if rnd in vmap and vmap[rnd] != mine:
            mismatches.append((rnd, mine, vmap[rnd]))
    missing = sorted(set(rows) - set(vmap))
    if mismatches:
        for m in mismatches:
            print("不一致:", m, file=sys.stderr)
        print("データソース間で当せん番号が一致しないため中止します。", file=sys.stderr)
        return 1

    expected = list(range(1, max(rows) + 1))
    gaps = sorted(set(expected) - set(rows))
    if gaps:
        print(f"欠番の回があります: {gaps[:20]}", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for rnd in sorted(rows):
            w.writerow(rows[rnd])
    print(
        f"保存しました: {OUT}  (第1回〜第{max(rows)}回, {len(rows)}回分, "
        f"照合一致 {len(rows) - len(missing)}回 / 照合データに無い回 {len(missing)}回)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
