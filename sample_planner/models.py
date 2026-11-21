"""领域对象：资料表行与方案评价结果。

金额一律使用整数分；日期使用 ISO 字符串并提供日期换算。
资料中的 ``null`` 表示"未取得该事实"，由加载层保留为 ``None``，
不与零混用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


@dataclass(frozen=True)
class Round:
    id: str
    deadline: date
    budget: int
    required_themes: tuple[str, ...]


@dataclass(frozen=True)
class Sample:
    """工坊的一个样品版本；同一 design 的多个版本只能选一个。"""

    id: str
    design: str
    material: str
    units: int
    shop: str
    hours: int
    start: date
    deadline: date
    themes: tuple[str, ...]

    @property
    def covers(self) -> frozenset[str]:
        """该版本实际能承诺的主题（换基材可能为空）。"""
        return frozenset(self.themes)


@dataclass(frozen=True)
class Offer:
    """供应商报价：对整批采购生效，按 lot_step 整步长下单。"""

    id: str
    material: str
    minimum: int
    lot_step: int
    lot_price: int
    valid_from: date
    valid_until: date
    arrives_on: date
    revision_of: Optional[str]

    def open_on(self, on_date: date) -> bool:
        """下单日是否落在报价有效期内（含端点）。"""
        return self.valid_from <= on_date <= self.valid_until


@dataclass(frozen=True)
class CapacityEntry:
    shop: str
    on: date
    hours: int
    known_on: date


@dataclass(frozen=True)
class Purchase:
    """已确认的整批采购；paid 是实际付款，可能与报价步长价不同。"""

    id: str
    round: str
    offer: str
    units: int
    paid: int
    refundable_units: int
    refund_per_unit: int
    committed_on: date

    @property
    def max_refund(self) -> int:
        return self.refundable_units * self.refund_per_unit


@dataclass(frozen=True)
class Hold:
    """工坊占位。confirmed 不可单方撤；tentative 允许换组。"""

    round: str
    sample: str
    shop: str
    on: date
    hours: int
    strength: str

    @property
    def confirmed(self) -> bool:
        return self.strength == "confirmed"


@dataclass(frozen=True)
class Stock:
    """某轮开账时继承到的原料余量（上一轮交接而来）。"""

    material: str
    units: int
    source_purchase: Optional[str] = None


@dataclass(frozen=True)
class LotOrder:
    """方案触发的一笔整批采购。"""

    offer: str
    material: str
    lots: int
    units: int
    cost: int


@dataclass(frozen=True)
class LeftoverLot:
    """结算后剩余的一批原料及其来源，供下一轮回查。"""

    material: str
    units: int
    source_purchase: Optional[str]  # 已付款采购编号；新报价补单为 None


@dataclass(frozen=True)
class MaterialBalance:
    """单一原料在一个方案内的合并账目：一份采购供所有符合用料的样品共用。"""

    material: str
    needed: int
    inherited_units: int
    existing_units: int
    new_order: Optional[LotOrder]
    consumed: int
    leftover: int
    leftover_purchase: Optional[str]
    refunded_units: int
    refund: int
    leftover_lots: tuple[LeftoverLot, ...] = ()

    @property
    def available(self) -> int:
        return self.existing_units + (
            self.new_order.units if self.new_order is not None else 0
        )


@dataclass(frozen=True)
class PlanEvaluation:
    """一个样品组合在同一轮预算与试卖截止日下的完整评价。"""

    round_id: str
    sample_ids: tuple[str, ...]
    as_of: date
    feasible: bool
    covers_themes: bool
    within_budget: bool
    gross_spend: int
    refunds: int
    net_cost: int
    headroom: int
    coverage: frozenset[str]
    missing_themes: tuple[str, ...]
    balances: tuple[MaterialBalance, ...]
    new_orders: tuple[LotOrder, ...]
    schedule: tuple[tuple[str, str, date, int], ...]
    blockers: tuple[str, ...]
    breaks_confirmed: tuple[str, ...]
    releases_tentative: tuple[str, ...]
    late_materials: tuple[str, ...]

    @property
    def actionable(self) -> bool:
        """无硬约束冲突且主题守得住（预算超支会单独标注，便于看取舍）。"""
        return self.feasible and self.covers_themes


@dataclass
class Drill:
    """整张演练资料的内存表示。"""

    rounds: list[Round] = field(default_factory=list)
    samples: list[Sample] = field(default_factory=list)
    offers: list[Offer] = field(default_factory=list)
    capacity: list[CapacityEntry] = field(default_factory=list)
    purchases: list[Purchase] = field(default_factory=list)
    holds: list[Hold] = field(default_factory=list)

    def round(self, round_id: str) -> Round:
        return next(item for item in self.rounds if item.id == round_id)

    def sample(self, sample_id: str) -> Sample:
        return next(item for item in self.samples if item.id == sample_id)

    def offer(self, offer_id: str) -> Offer:
        return next(item for item in self.offers if item.id == offer_id)
