"""正規化したデータの型。

ルール（ケイバ進化録の仕様に合わせる）
- 仮想（SHADOW / CONTROLの仮想BUY）と実馬券は別の型にして、絶対に合算しない。
- ROIは「精算済み」のレースだけで計算する。保留（同着・返還・取消など）は未精算額として別に持つ。
- 読めなかった値は None のまま。0 で埋めない。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

SETTLED = "SETTLED"
HELD = "HELD"
EXCLUDED = "EXCLUDED"

GATES = ("CORE", "WATCH_CHAOS", "WATCH_NEAR", "PASS")
FAILURE_STAGES = ("selection", "role", "theory", "compression")
FAILURE_LABELS = {
    "selection": "レース選定",
    "role": "役割（HEAD/SECOND/THIRD）",
    "theory": "理論券",
    "compression": "圧縮",
}
ROLES = ("HEAD", "SECOND", "THIRD")
BANDS = (200_000, 500_000, 1_000_000, 5_000_000)


@dataclass
class RaceRecord:
    """仮想購入の1レース分（またはレース明細が無い日の集計1件）。"""

    day: date
    market: str  # "JRA" / "Local"
    system: str  # cohort名。例 "Local Set01 / CONTROL v0"
    race_id: str
    status: str = SETTLED
    gate: str | None = None
    venue: str | None = None
    stake: int | None = None  # 仮想投資（円）
    payout: int | None = None  # 仮想払戻（円）
    trifecta_payout_per100: int | None = None  # 公式三連単払戻（100円あたり）
    primary_failure: str | None = None
    missed_roles: list[str] = field(default_factory=list)
    # レース明細が無く日次集計だけ読めた場合 True。件数は下の2つを使う。
    is_aggregate: bool = False
    target_count: int = 1
    buy_count: int | None = None
    source_file: str | None = None

    @property
    def buys(self) -> int:
        if self.buy_count is not None:
            return self.buy_count
        return 1 if (self.stake or 0) > 0 else 0

    @property
    def hit(self) -> bool:
        return (self.payout or 0) > 0


@dataclass
class ActualBet:
    """実際に買った馬券（JRA・楽天競馬・SPAT4など）。"""

    day: date
    source: str
    race_id: str
    bet_type: str | None
    stake: int
    payout: int | None  # 未確定は None
    status: str = SETTLED  # SETTLED / REFUND / PENDING


@dataclass
class CapitalSnapshot:
    cohort: str
    level: str | None
    confidence: str | None
    decision: str | None
    as_of: str | None = None


@dataclass
class Dataset:
    races: list[RaceRecord] = field(default_factory=list)
    actual_bets: list[ActualBet] = field(default_factory=list)
    capital: list[CapitalSnapshot] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def extend(self, other: "Dataset") -> None:
        self.races.extend(other.races)
        self.actual_bets.extend(other.actual_bets)
        self.capital.extend(other.capital)
        self.warnings.extend(other.warnings)
