#!/usr/bin/env python3
"""負けの研究室：週次レポート・動画台本の自動生成。

使い方
  python weekly_report.py build --config config.json            # 先週分（月〜日）を作る
  python weekly_report.py build --config config.json --week 2026-10-26
  python weekly_report.py inspect "C:\\...\\Local_Prospective\\runs"  # JSONの構造だけ表示

外部ライブラリは使わない（Python 3.10 以上の標準ライブラリだけ）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from wr import __version__  # noqa: E402
from wr.adapters import inspect_paths, load_sources, to_date  # noqa: E402
from wr.lint import find_violations  # noqa: E402
from wr.metrics import compute, last_completed_week, week_bounds  # noqa: E402
from wr.render import render_all  # noqa: E402

JST = timezone(timedelta(hours=9))  # Windowsでtzdataが無くても動くよう固定オフセット


def _json_default(o):
    if isinstance(o, date):
        return o.isoformat()
    raise TypeError(type(o).__name__)


def build(args: argparse.Namespace) -> int:
    cfg_path = Path(args.config).resolve()
    cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    base = cfg_path.parent

    today = to_date(args.today) if args.today else datetime.now(JST).date()
    if args.week:
        d = to_date(args.week)
        if d is None:
            print(f"--week の日付が読めません: {args.week}", file=sys.stderr)
            return 1
        start, end = week_bounds(d)
    else:
        start, end = last_completed_week(today)
    since = to_date(cfg.get("start_date")) if cfg.get("start_date") else None

    ds = load_sources(cfg.get("sources", []), base)
    metrics = compute(ds, start, end, since)
    files = render_all(metrics, cfg)

    problems = []
    for name, text in files.items():
        problems += [f"{name}: {v}" for v in find_violations(text)]
    if problems:
        print("表現チェックで止めました（何も書き出していません）：", file=sys.stderr)
        for p in problems:
            print("  " + p, file=sys.stderr)
        return 2

    out_root = Path(args.out or cfg.get("output_dir", "weekly_out"))
    if not out_root.is_absolute():
        out_root = base / out_root
    out_dir = out_root / start.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out_dir / name).write_text(text, encoding="utf-8", newline="\n")
    summary = {"tool_version": __version__, "generated_at": datetime.now(JST).isoformat(),
               **metrics}
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8")

    print(f"出力しました: {out_dir}")
    for name in [*files, "summary.json"]:
        print(f"  - {name}")
    if not metrics["cohorts"] and not metrics["has_actual_bets"]:
        print("注意：集計できるデータが0件でした。config.json の root と、inspect の結果を確認してください。")
    if metrics["warnings"]:
        print(f"データ注記 {len(metrics['warnings'])}件（report_note.md の末尾に記載）")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="負けの研究室 週次レポート・動画台本の自動生成")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="週次レポートと台本を作る")
    b.add_argument("--config", required=True, help="config.json のパス")
    b.add_argument("--week", help="その週に含まれる任意の日付（省略時は先週の月〜日）")
    b.add_argument("--out", help="出力先フォルダ（省略時は config の output_dir）")
    b.add_argument("--today", help=argparse.SUPPRESS)  # テスト用
    b.set_defaults(func=build)

    i = sub.add_parser("inspect", help="JSONの構造だけを表示（値は伏せる）")
    i.add_argument("paths", nargs="+")
    i.add_argument("--limit", type=int, default=3, help="同じ形のファイルを何件まで表示するか")
    i.set_defaults(func=lambda a: (print(inspect_paths(a.paths, a.limit)), 0)[1])

    # Windowsのコンソール（cp932）で出せない記号があっても落ちないようにする
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
