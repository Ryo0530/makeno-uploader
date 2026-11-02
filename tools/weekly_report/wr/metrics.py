"""週次・累計の集計。仮想（cohortごと）と実馬券を別々に集計し、合算しない。"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from .model import (
    BANDS,
    EXCLUDED,
    FAILURE_STAGES,
    HELD,
    ROLES,
    SETTLED,
    ActualBet,
    Dataset,
    RaceRecord,
)


def week_bounds(any_day: date) -> tuple[date, date]:
    """月曜はじまり・日曜おわり。"""
    start = any_day - timedelta(days=any_day.weekday())
    return start, start + timedelta(days=6)


def last_completed_week(today: date) -> tuple[date, date]:
    return week_bounds(today - timedelta(days=today.weekday() + 1))


def roi(payout: int, stake: int) -> float | None:
    return None if stake <= 0 else payout / stake


@dataclass
class Stats:
    target_races: int = 0  # 精算済みの対象R
    buy_races: int = 0
    hit_races: int = 0
    stake: int = 0
    payout: int = 0
    held_races: int = 0
    held_stake: int = 0
    excluded_races: int = 0
    missing_values: int = 0  # 精算済みなのに投資/払戻が読めなかった件数
    max_single_payout: int = 0  # いちばん大きい当たり1本（明細があるRのみ）
    gate_counts: dict = field(default_factory=dict)

    @property
    def profit(self) -> int:
        return self.payout - self.stake

    @property
    def roi(self) -> float | None:
        return roi(self.payout, self.stake)

    @property
    def roi_ex_top1(self) -> float | None:
        """最大の当たり1本を除いた回収率。分母は元の投資のまま（Capital SPECと同じ保守的な定義）。"""
        return roi(self.payout - self.max_single_payout, self.stake)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["profit"] = self.profit
        d["roi"] = self.roi
        d["roi_ex_top1"] = self.roi_ex_top1
        return d


def _stats(races: list[RaceRecord]) -> Stats:
    s = Stats()
    gates: Counter = Counter()
    for r in races:
        if r.status == EXCLUDED:
            s.excluded_races += r.target_count
            continue
        if r.status == HELD:
            s.held_races += r.target_count
            s.held_stake += r.stake or 0
            continue
        s.target_races += r.target_count
        if r.gate:
            gates[r.gate] += r.target_count
        if r.stake is None or (r.buys and r.payout is None):
            s.missing_values += 1
        stake = r.stake or 0
        if stake <= 0 and not r.is_aggregate:
            continue
        s.buy_races += r.buys
        s.stake += stake
        s.payout += r.payout or 0
        if not r.is_aggregate and r.hit:
            s.hit_races += 1
            s.max_single_payout = max(s.max_single_payout, r.payout or 0)
    s.gate_counts = dict(gates)
    return s


def _losing_streak(races: list[RaceRecord]) -> int | None:
    buys = sorted((r for r in races if r.status == SETTLED and not r.is_aggregate and (r.stake or 0) > 0),
                  key=lambda r: (r.day, r.race_id))
    if not buys:
        return None
    n = 0
    for r in reversed(buys):
        if r.hit:
            break
        n += 1
    return n


def _failure_breakdown(races: list[RaceRecord]) -> dict:
    misses = [r for r in races if r.status == SETTLED and not r.is_aggregate and not r.hit]
    stages = Counter(r.primary_failure for r in misses if r.primary_failure)
    roles = Counter(role for r in misses for role in r.missed_roles)
    return {
        "missed_races": len(misses),
        "classified": sum(stages.values()),
        "stages": {k: stages.get(k, 0) for k in FAILURE_STAGES},
        "missed_roles": {k: roles.get(k, 0) for k in ROLES},
    }


def _bands(races: list[RaceRecord]) -> list[dict]:
    known = [r for r in races if r.status == SETTLED and not r.is_aggregate
             and r.trifecta_payout_per100 is not None]
    out = []
    for th in BANDS:
        occ = [r for r in known if r.trifecta_payout_per100 >= th]
        cap = [r for r in occ if (r.stake or 0) > 0 and r.hit]
        out.append({"threshold": th, "occurred": len(occ), "captured": len(cap),
                    "known_races": len(known)})
    return out


def _highlights(races: list[RaceRecord]) -> dict:
    detail = [r for r in races if r.status == SETTLED and not r.is_aggregate]
    best = max((r for r in detail if r.hit), key=lambda r: r.payout or 0, default=None)
    biggest_miss = max((r for r in detail if not r.hit and (r.stake or 0) > 0),
                       key=lambda r: r.stake or 0, default=None)
    upset = max((r for r in detail if r.trifecta_payout_per100 is not None),
                key=lambda r: r.trifecta_payout_per100, default=None)
    by_day: dict[date, int] = defaultdict(int)
    for r in races:
        if r.status == SETTLED and (r.stake or 0) > 0:
            by_day[r.day] += (r.payout or 0) - (r.stake or 0)
    worst_day = min(by_day.items(), key=lambda kv: kv[1], default=None)

    def brief(r: RaceRecord | None) -> dict | None:
        if r is None:
            return None
        return {"day": r.day.isoformat(), "race_id": r.race_id, "venue": r.venue,
                "gate": r.gate, "stake": r.stake, "payout": r.payout,
                "trifecta_payout_per100": r.trifecta_payout_per100,
                "primary_failure": r.primary_failure, "missed_roles": r.missed_roles}

    return {
        "best_hit": brief(best),
        "biggest_miss": brief(biggest_miss),
        "biggest_upset": brief(upset),
        "worst_day": ({"day": worst_day[0].isoformat(), "profit": worst_day[1]}
                      if worst_day and worst_day[1] < 0 else None),
    }


def _actual(bets: list[ActualBet]) -> dict:
    def agg(items: list[ActualBet]) -> dict:
        stake = payout = pending = refunds = 0
        settled_n = 0
        for b in items:
            if b.status == "PENDING":
                pending += b.stake
                continue
            if b.status == "REFUND":
                refunds += 1
            settled_n += 1
            stake += b.stake
            # 返還で払戻が空のときは、返還＝購入額の払い戻しとして扱う
            payout += b.payout if b.payout is not None else (b.stake if b.status == "REFUND" else 0)
        return {"bets": settled_n, "stake": stake, "payout": payout, "profit": payout - stake,
                "roi": roi(payout, stake), "pending_stake": pending, "refunds": refunds}

    by_source: dict[str, list[ActualBet]] = defaultdict(list)
    for b in bets:
        by_source[b.source].append(b)
    return {"total": agg(bets), "by_source": {k: agg(v) for k, v in sorted(by_source.items())}}


def compute(ds: Dataset, week_start: date, week_end: date, since: date | None = None) -> dict:
    in_week = lambda d: week_start <= d <= week_end  # noqa: E731
    in_cum = lambda d: (since is None or d >= since) and d <= week_end  # noqa: E731

    cohorts: dict[tuple[str, str], list[RaceRecord]] = defaultdict(list)
    for r in ds.races:
        cohorts[(r.market, r.system)].append(r)

    cohort_out = []
    for (market, system), races in sorted(cohorts.items()):
        week = [r for r in races if in_week(r.day)]
        cum = [r for r in races if in_cum(r.day)]
        cohort_out.append({
            "market": market,
            "system": system,
            "week": _stats(week).to_dict(),
            "cumulative": _stats(cum).to_dict(),
            "cumulative_days": len({r.day for r in cum if r.status == SETTLED}),
            "first_day": min((r.day for r in cum), default=None),
            "losing_streak": _losing_streak(cum),
            "failures": _failure_breakdown(week),
            "bands": _bands(week),
            "highlights": _highlights(week),
            "has_aggregate_only_days": any(r.is_aggregate for r in week),
        })
    for c in cohort_out:
        if c["first_day"] is not None:
            c["first_day"] = c["first_day"].isoformat()

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "since": since.isoformat() if since else None,
        "cohorts": cohort_out,
        "actual_week": _actual([b for b in ds.actual_bets if in_week(b.day)]),
        "actual_cumulative": _actual([b for b in ds.actual_bets if in_cum(b.day)]),
        "has_actual_bets": bool(ds.actual_bets),
        "capital": [asdict(c) for c in ds.capital],
        "warnings": list(ds.warnings),
    }
