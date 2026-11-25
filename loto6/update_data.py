#!/usr/bin/env python3
"""ロト6の過去当せんデータを取得・照合して data/loto6.csv に保存する。

公式サイトはこの環境から自動取得できないため、GitHub上で公開されている
独立したデータセットを取得し、本数字・ボーナス数字・抽せん日が全回で一致することを確認してから保存する。

  主ソース  : Wantarot/loto6_analyzer         data/loto6_history.csv (当せん口数・金額・キャリーオーバー付き)
  照合ソース: nodvex/loto6-analyzer            data/loto6.json
              raewonkimdev-eng/kaiun-loto-data input/loto6.csv

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

PRIMARY_URL = "https://raw.githubusercontent.com/Wantarot/loto6_analyzer/main/data/loto6_history.csv"
VERIFY_JSON_URL = "https://raw.githubusercontent.com/nodvex/loto6-analyzer/main/data/loto6.json"
VERIFY_CSV_URL = "https://raw.githubusercontent.com/raewonkimdev-eng/kaiun-loto-data/main/input/loto6.csv"
OUT = Path(__file__).resolve().parent / "data" / "loto6.csv"

FIELDS = (
    ["round", "date"]
    + [f"n{i}" for i in range(1, 7)]
    + ["b1"]
    + [f"w{k}" for k in range(1, 6)]
    + [f"p{k}" for k in range(1, 6)]
    + ["carryover"]
)


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "loto6-analysis/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8-sig")


def num(s: str) -> int:
    s = (s or "").strip().replace(",", "")
    return int(s) if s else 0


def parse_primary(text: str) -> dict[int, dict]:
    rows = {}
    for r in csv.DictReader(io.StringIO(text)):
        rnd = int(r["draw_no"])
        nums = sorted(int(r[f"num{i}"]) for i in range(1, 7))
        bonus = int(r["bonus"])
        if len(set(nums + [bonus])) != 7 or not all(1 <= x <= 43 for x in nums + [bonus]):
            raise ValueError(f"第{rnd}回: 数字が不正 {nums} {bonus}")
        row = {"round": rnd, "date": r["draw_date"].strip()}
        row.update({f"n{i+1}": v for i, v in enumerate(nums)})
        row["b1"] = bonus
        for k in range(1, 6):
            row[f"w{k}"] = num(r[f"prize{k}_count"])
            row[f"p{k}"] = num(r[f"prize{k}_amount"])
        row["carryover"] = num(r["carryover"])
        rows[rnd] = row
    return rows


def parse_verify_csv(text: str) -> dict[int, tuple]:
    out = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        p = line.strip().split(",")
        out[int(p[0])] = (sorted(map(int, p[2].split())), int(p[3]), p[1])
    return out


def main() -> int:
    print(f"取得中: {PRIMARY_URL}")
    rows = parse_primary(fetch(PRIMARY_URL))
    mine = {k: ([r[f"n{i}"] for i in range(1, 7)], r["b1"], r["date"]) for k, r in rows.items()}

    sources = []
    try:
        print(f"照合用データ取得中: {VERIFY_JSON_URL}")
        sources.append(("nodvex", {d["draw"]: (sorted(d["numbers"]), d["bonus"], d["date"])
                                   for d in json.loads(fetch(VERIFY_JSON_URL))}))
    except Exception as e:  # noqa: BLE001
        print(f"  取得失敗: {e}")
    try:
        print(f"照合用データ取得中: {VERIFY_CSV_URL}")
        sources.append(("kaiun", parse_verify_csv(fetch(VERIFY_CSV_URL))))
    except Exception as e:  # noqa: BLE001
        print(f"  取得失敗: {e}")
    if not sources:
        print("照合用データが1つも取れなかったため中止します。", file=sys.stderr)
        return 1

    for name, v in sources:
        bad = [k for k in mine if k in v and v[k] != mine[k]]
        if bad:
            for k in bad[:10]:
                print(f"不一致 ({name}) 第{k}回: {mine[k]} / {v[k]}", file=sys.stderr)
            print("データソース間で当せん番号が一致しないため中止します。", file=sys.stderr)
            return 1
        print(f"  {name}: {sum(1 for k in mine if k in v)}回分 一致")

    gaps = sorted(set(range(1, max(rows) + 1)) - set(rows))
    if gaps:
        print(f"欠番の回があります: {gaps[:20]}", file=sys.stderr)
        return 1
    # 最新回が照合ソースのどれにも無い場合は、確認できるまで保存しない
    latest = max(rows)
    if not any(latest in v for _, v in sources):
        print(f"第{latest}回がまだ照合用データに無いため、確認できた回までで保存します。")
        rows = {k: r for k, r in rows.items() if any(k in v for _, v in sources)}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for rnd in sorted(rows):
            w.writerow(rows[rnd])
    print(f"保存しました: {OUT}  (第1回〜第{max(rows)}回, {len(rows)}回分)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
