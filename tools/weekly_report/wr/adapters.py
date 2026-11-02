"""ケイバ進化録の出力を読み込んで Dataset に正規化する。

実データのJSONの形はこちらで直接確認できていないため、仕様書（Local CONTROL v0 SPEC、
Local Set01 運用手順書、Capital Level SPEC）に出てくる項目名と、よくある表記ゆれを
候補として探す。見つからない値は推測で埋めず None にして warnings に残す。

対応するソース
- local_set01 : <root>/runs/prospective/<日付>/evaluation/report.json
- capital     : <root>/reports/latest.json
- normalized  : races.csv / actual_bets.csv / capital.json（手で書き出す共通形式）
"""
from __future__ import annotations

import csv
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from .model import (
    EXCLUDED,
    HELD,
    SETTLED,
    ActualBet,
    CapitalSnapshot,
    Dataset,
    RaceRecord,
)

# ---------------------------------------------------------------- helpers


def _norm_key(k: str) -> str:
    return re.sub(r"[\s_\-]", "", str(k)).lower()


def get(d: Any, *keys: str, default: Any = None) -> Any:
    """大文字小文字・_・- を無視してキーを探す。"a.b" で入れ子も辿る。"""
    if not isinstance(d, dict):
        return default
    index = {_norm_key(k): v for k, v in d.items()}
    for key in keys:
        if "." in key:
            head, rest = key.split(".", 1)
            sub = get(d, head)
            val = get(sub, rest)
        else:
            val = index.get(_norm_key(key))
        if val is not None and val != "":
            return val
    return default


def to_int(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(round(v))
    s = str(v).strip().replace(",", "").replace("円", "").replace("¥", "")
    if s == "" or s.lower() in ("null", "none", "nan", "-", "—", "unknown"):
        return None
    try:
        return int(round(float(s)))
    except ValueError:
        return None


_DATE_RE = re.compile(r"(20\d{2})[-_/]?(\d{2})[-_/]?(\d{2})")


def to_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    m = _DATE_RE.search(str(v))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# 判定順が大事："unsettled" は "settled" を含むので HELD を先に見る。
# DAILY_BUDGET_SKIP は「対象レースだが予算で買わなかった」なので精算済み（投資0）扱い。
_STATUS_ORDER = (
    (SETTLED, ("dailybudgetskip", "budgetskip", "nobuy")),
    (HELD, ("unsettled", "held", "hold", "pending", "paused", "保留", "未精算",
            "deadheat", "refund", "返還", "同着", "取消", "中止")),
    (EXCLUDED, ("excluded", "exclude", "skip", "notarget", "対象外", "除外", "invalid")),
    (SETTLED, ("settled", "normal", "ok", "done", "evaluated", "official", "通常", "精算済")),
)


def to_status(v: Any, default: str = SETTLED) -> str:
    if v is None:
        return default
    s = _norm_key(v)
    for status, words in _STATUS_ORDER:
        if any(w in s for w in words):
            return status
    return default


_FAILURE_MAP = {
    "selection": ("selection", "select", "race", "gate", "選定"),
    "role": ("role", "役割", "head", "second", "third"),
    "theory": ("theory", "理論"),
    "compression": ("compression", "compress", "圧縮"),
}


def to_failure(v: Any) -> str | None:
    if v is None:
        return None
    s = _norm_key(v)
    for stage, words in _FAILURE_MAP.items():
        if any(w in s for w in words):
            return stage
    return None


def to_roles(v: Any) -> list[str]:
    if v is None:
        return []
    items = v if isinstance(v, list) else re.split(r"[;,/\s]+", str(v))
    out = []
    for item in items:
        s = str(item).strip().upper()
        if s in ("HEAD", "SECOND", "THIRD") and s not in out:
            out.append(s)
    return out


def _read_json(path: Path) -> Any:
    # Windows で作られた BOM 付き UTF-8 にも対応
    return json.loads(path.read_text(encoding="utf-8-sig"))


# ------------------------------------------------------------ local_set01

_RACE_LIST_KEYS = ("races", "race_results", "results", "per_race", "race_reports",
                   "evaluations", "race_evaluations", "records")


def _race_items(report: dict) -> list[dict] | None:
    for key in _RACE_LIST_KEYS:
        val = get(report, key)
        if isinstance(val, list) and all(isinstance(x, dict) for x in val):
            return val
        if isinstance(val, dict) and val and all(isinstance(x, dict) for x in val.values()):
            return [{"race_id": k, **v} for k, v in val.items()]
    return None


def _stake_of(item: dict) -> int | None:
    stake = to_int(get(item, "stake", "investment", "virtual_stake", "virtual_investment",
                       "cost", "buy_amount", "投資", "投資額"))
    if stake is None:
        points = to_int(get(item, "points", "tickets", "ticket_count", "buy_points", "点数"))
        if points is not None:
            stake = points * 100  # 仕様：1点100円
    return stake


def parse_local_report(report: dict, *, day: date, market: str, system: str,
                       source_file: str) -> tuple[list[RaceRecord], list[str]]:
    warnings: list[str] = []
    items = _race_items(report)
    records: list[RaceRecord] = []
    if items is not None:
        for i, item in enumerate(items):
            race_id = str(get(item, "race_id", "raceid", "id", "race", default=f"{day}-{i + 1}"))
            gate = get(item, "gate", "category", "class", "classification", "区分")
            records.append(RaceRecord(
                day=day, market=market, system=system, race_id=race_id,
                status=to_status(get(item, "status", "settlement_status", "settlement",
                                     "state", "result_status")),
                gate=str(gate).upper() if gate is not None else None,
                venue=get(item, "venue", "track", "course", "場", "場名"),
                stake=_stake_of(item),
                payout=to_int(get(item, "payout", "return", "virtual_payout", "refund_amount",
                                  "払戻", "払戻金")),
                trifecta_payout_per100=to_int(get(item, "trifecta_payout", "official_trifecta_payout",
                                                  "trifecta_payout_per100", "payout_per100",
                                                  "sanrentan_payout", "三連単払戻")),
                primary_failure=to_failure(get(item, "primary_failure", "failure",
                                               "failure_stage", "main_failure", "主失敗")),
                missed_roles=to_roles(get(item, "missed_roles", "missed_role")),
                source_file=source_file,
            ))
        return records, warnings

    # レース明細が無い：日次集計だけ拾う
    summary = get(report, "summary", "totals", "day_summary", "aggregate") or report
    stake = to_int(get(summary, "investment", "stake", "total_investment", "virtual_investment"))
    payout = to_int(get(summary, "payout", "total_payout", "return", "virtual_payout"))
    settled = to_int(get(summary, "settled_races", "blind_races", "n_settled", "normal_races",
                         "evaluated_races", "races"))
    buys = to_int(get(summary, "buy_races", "buy_count", "n_buy", "buys"))
    held = to_int(get(summary, "held_races", "pending_races", "n_held"))
    held_stake = to_int(get(summary, "unsettled_amount", "held_investment", "pending_investment",
                            "unsettled_investment"))
    if stake is None and payout is None and settled is None:
        warnings.append(f"{source_file}: レース明細も日次集計も見つからず読み飛ばしました（--inspect で構造を確認してください）")
        return records, warnings
    warnings.append(f"{source_file}: レース明細が無いため日次集計だけ使いました（失敗分析・高配当帯は集計対象外）")
    records.append(RaceRecord(
        day=day, market=market, system=system, race_id=f"{day}-SUMMARY", status=SETTLED,
        stake=stake, payout=payout, is_aggregate=True,
        target_count=settled if settled is not None else 0, buy_count=buys,
        source_file=source_file,
    ))
    if held or held_stake:
        records.append(RaceRecord(
            day=day, market=market, system=system, race_id=f"{day}-HELD", status=HELD,
            stake=held_stake, is_aggregate=True, target_count=held or 0,
            buy_count=None, source_file=source_file,
        ))
    return records, warnings


def load_local_set01(root: str | Path, *, market: str = "Local",
                     system: str = "Local Set01 / CONTROL v0") -> Dataset:
    ds = Dataset()
    base = Path(root) / "runs" / "prospective"
    if not base.is_dir():
        ds.warnings.append(f"{base}: フォルダがありません（local_set01）")
        return ds
    for report_path in sorted(base.glob("*/evaluation/report.json")):
        rel = str(report_path)
        try:
            report = _read_json(report_path)
        except (OSError, ValueError) as e:
            ds.warnings.append(f"{rel}: 読めませんでした（{e}）")
            continue
        if not isinstance(report, dict):
            ds.warnings.append(f"{rel}: JSONの最上位がオブジェクトではないため読み飛ばしました")
            continue
        day = to_date(get(report, "date", "race_date", "target_date", "day", "meeting_date")) \
            or to_date(report_path.parent.parent.name)
        if day is None:
            ds.warnings.append(f"{rel}: 日付がわからないため読み飛ばしました")
            continue
        recs, warns = parse_local_report(report, day=day, market=market, system=system,
                                         source_file=rel)
        ds.races.extend(recs)
        ds.warnings.extend(warns)
    return ds


# ---------------------------------------------------------------- capital


def parse_capital(report: Any) -> list[CapitalSnapshot]:
    as_of = get(report, "generated_at", "as_of", "evaluated_at", "date", "timestamp") \
        if isinstance(report, dict) else None
    entries: list[tuple[str, dict]] = []
    if isinstance(report, dict):
        cohorts = get(report, "cohorts", "results", "levels", "markets")
        if isinstance(cohorts, list):
            entries = [(str(get(c, "cohort", "name", "market", "id", default=f"cohort{i + 1}")), c)
                       for i, c in enumerate(cohorts) if isinstance(c, dict)]
        elif isinstance(cohorts, dict):
            entries = [(k, v) for k, v in cohorts.items() if isinstance(v, dict)]
        else:
            entries = [(k, v) for k, v in report.items()
                       if isinstance(v, dict) and get(v, "level", "recommended_level", "eligible_level")]
            if not entries and get(report, "level", "recommended_level"):
                entries = [(str(get(report, "cohort", "name", default="capital")), report)]
    elif isinstance(report, list):
        entries = [(str(get(c, "cohort", "name", "market", default=f"cohort{i + 1}")), c)
                   for i, c in enumerate(report) if isinstance(c, dict)]
    out = []
    for name, c in entries:
        out.append(CapitalSnapshot(
            cohort=name,
            level=_s(get(c, "level", "recommended_level", "eligible_level", "current_level")),
            confidence=_s(get(c, "confidence")),
            decision=_s(get(c, "decision", "state", "status", "verdict")),
            as_of=_s(as_of),
        ))
    return out


def _s(v: Any) -> str | None:
    return None if v is None else str(v)


def load_capital(root: str | Path) -> Dataset:
    ds = Dataset()
    path = Path(root) / "reports" / "latest.json"
    if not path.is_file():
        ds.warnings.append(f"{path}: ファイルがありません（capital）")
        return ds
    try:
        snaps = parse_capital(_read_json(path))
    except (OSError, ValueError) as e:
        ds.warnings.append(f"{path}: 読めませんでした（{e}）")
        return ds
    if not snaps:
        ds.warnings.append(f"{path}: 資金レベルの項目が見つかりませんでした（--inspect で構造を確認してください）")
    ds.capital.extend(snaps)
    return ds


# ------------------------------------------------------------- normalized


def _read_csv(path: Path) -> Iterable[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        yield from csv.DictReader(f)


def load_normalized(directory: str | Path, *, default_system: str | None = None) -> Dataset:
    ds = Dataset()
    d = Path(directory)
    races = d / "races.csv"
    if races.is_file():
        for n, row in enumerate(_read_csv(races), start=2):
            day = to_date(row.get("date"))
            if day is None or not row.get("race_id"):
                ds.warnings.append(f"{races}:{n}: date か race_id が無いため読み飛ばしました")
                continue
            ds.races.append(RaceRecord(
                day=day,
                market=(row.get("market") or "Local").strip(),
                system=(row.get("system") or default_system or "normalized").strip(),
                race_id=row["race_id"].strip(),
                status=to_status(row.get("status")),
                gate=(row.get("gate") or "").strip().upper() or None,
                venue=(row.get("venue") or "").strip() or None,
                stake=to_int(row.get("stake")),
                payout=to_int(row.get("payout")),
                trifecta_payout_per100=to_int(row.get("trifecta_payout_per100")),
                primary_failure=to_failure(row.get("primary_failure")),
                missed_roles=to_roles(row.get("missed_roles")),
                source_file=str(races),
            ))
    bets = d / "actual_bets.csv"
    if bets.is_file():
        for n, row in enumerate(_read_csv(bets), start=2):
            day = to_date(row.get("date"))
            stake = to_int(row.get("stake"))
            if day is None or stake is None:
                ds.warnings.append(f"{bets}:{n}: date か stake が無いため読み飛ばしました")
                continue
            raw_status = _norm_key(row.get("status") or "")
            status = ("PENDING" if any(w in raw_status for w in ("pending", "未確定"))
                      else "REFUND" if any(w in raw_status for w in ("refund", "返還"))
                      else SETTLED)
            payout = to_int(row.get("payout"))
            if status == SETTLED and payout is None:
                ds.warnings.append(f"{bets}:{n}: 払戻が空なので未確定（PENDING）として扱いました")
                status = "PENDING"
            ds.actual_bets.append(ActualBet(
                day=day, source=(row.get("source") or "不明").strip(),
                race_id=(row.get("race_id") or "").strip(),
                bet_type=(row.get("bet_type") or "").strip() or None,
                stake=stake, payout=payout, status=status,
            ))
    cap = d / "capital.json"
    if cap.is_file():
        try:
            ds.capital.extend(parse_capital(_read_json(cap)))
        except (OSError, ValueError) as e:
            ds.warnings.append(f"{cap}: 読めませんでした（{e}）")
    if not (races.is_file() or bets.is_file() or cap.is_file()):
        ds.warnings.append(f"{d}: races.csv / actual_bets.csv / capital.json のどれもありません")
    return ds


# ------------------------------------------------------------------ entry


def load_sources(sources: list[dict], base_dir: Path) -> Dataset:
    ds = Dataset()
    for src in sources:
        kind = src.get("type")
        path = src.get("root") or src.get("dir")
        if not path:
            ds.warnings.append(f"ソース設定に root / dir がありません: {src}")
            continue
        p = Path(path)
        if not p.is_absolute():
            p = base_dir / p
        if kind == "local_set01":
            ds.extend(load_local_set01(p, market=src.get("market", "Local"),
                                       system=src.get("system", "Local Set01 / CONTROL v0")))
        elif kind == "capital":
            ds.extend(load_capital(p))
        elif kind == "normalized":
            ds.extend(load_normalized(p, default_system=src.get("system")))
        else:
            ds.warnings.append(f"未対応のソース種別です: {kind}")
    ds.races = _dedupe(ds.races, ds.warnings)
    return ds


def _dedupe(races: list[RaceRecord], warnings: list[str]) -> list[RaceRecord]:
    """同じ cohort・日付・race_id が2回出たら後のものを使う（同日再実行の二重計上防止）。"""
    seen: dict[tuple, int] = {}
    out: list[RaceRecord] = []
    for r in races:
        key = (r.system, r.day, r.race_id)
        if key in seen:
            warnings.append(f"重複したレースを1件にまとめました: {r.system} {r.day} {r.race_id}")
            out[seen[key]] = r
        else:
            seen[key] = len(out)
            out.append(r)
    return out


# ---------------------------------------------------------------- inspect


def skeleton(value: Any, depth: int = 0, max_depth: int = 6) -> Any:
    """値を伏せてJSONの構造だけを返す（短い文字列だけは区分名の確認用に残す）。"""
    if depth >= max_depth:
        return "…"
    if isinstance(value, dict):
        return {k: skeleton(v, depth + 1, max_depth) for k, v in value.items()}
    if isinstance(value, list):
        if not value:
            return "list[0]"
        return [f"list[{len(value)}]", skeleton(value[0], depth + 1, max_depth)]
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return type(value).__name__
    if value is None:
        return "null"
    s = str(value)
    return f"str:{s}" if len(s) <= 24 else f"str({len(s)})"


def inspect_paths(paths: list[str], limit: int = 3) -> str:
    lines = []
    for raw in paths:
        p = Path(raw)
        files = [p] if p.is_file() else sorted(p.rglob("*.json"))
        if not files:
            lines.append(f"# {p}: JSONが見つかりません")
            continue
        groups: dict[str, list[Path]] = {}
        for f in files:
            # 日付フォルダごとに同じ形のファイルが並ぶので、パターンごとに数件だけ出す
            pattern = re.sub(r"20\d{2}[-_]?\d{2}[-_]?\d{2}", "<date>", str(f.relative_to(p) if p.is_dir() else f.name))
            groups.setdefault(pattern, []).append(f)
        for pattern, fs in sorted(groups.items()):
            lines.append(f"# {pattern}  （{len(fs)}件中 {min(len(fs), limit)}件を表示）")
            for f in fs[-limit:]:
                try:
                    sk = skeleton(_read_json(f))
                except (OSError, ValueError) as e:
                    sk = f"読めませんでした: {e}"
                lines.append(f"## {f.name}")
                lines.append(json.dumps(sk, ensure_ascii=False, indent=1))
    return "\n".join(lines)
