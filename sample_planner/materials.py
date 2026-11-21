"""整批采购账目：步长报价、共享原料池、退货回收与余量交接。

规则要点：
- 供应商按整批报价：下单量必须满足 minimum，并按 lot_step 取整步长，
  成本 = 步数 × lot_price，不能把各样品的单位报价简单相加。
- 一笔原料采购由所有同料样品共用：先合并总需求再决定补单，
  超出某个样品需求的余量仍在采购账上（不能免费忽略），
  也不会被重复计给下一个方案（每个方案独立从零重算）。
- 已付款且不可退的原料始终占用本轮预算；可退部分按退货条件回收。
  即使最新组合不再使用某材料，付款事实仍然保留，只是以退款形式冲减。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .availability import is_orderable, latest_revision
from .models import (
    Drill,
    LeftoverLot,
    LotOrder,
    MaterialBalance,
    Offer,
    Purchase,
    Sample,
    Stock,
)


def lots_needed(offer: Offer, units_needed: int) -> int:
    """满足需求所需的最少步长数（同时受 minimum 约束）。"""
    target = max(units_needed, offer.minimum)
    return (target + offer.lot_step - 1) // offer.lot_step


def quote_order(offer: Offer, units_needed: int) -> LotOrder:
    lots = lots_needed(offer, units_needed)
    return LotOrder(
        offer=offer.id,
        material=offer.material,
        lots=lots,
        units=lots * offer.lot_step,
        cost=lots * offer.lot_price,
    )


def cheapest_order(
    drill: Drill, material: str, shortfall: int, as_of: date, needed_by: date
) -> tuple[LotOrder | None, list[str]]:
    """在当期可下单报价中选最便宜的整批补单。

    返回 (订单, 不可用原因列表)。晚到/过期/被替代的报价只作为诊断信息，
    不进入可行选项。
    """
    candidates: list[LotOrder] = []
    reasons: list[str] = []
    for offer in drill.offers:
        if offer.material != material:
            continue
        order = quote_order(offer, shortfall)
        if not is_orderable(offer, drill, as_of, needed_by=needed_by):
            latest = latest_revision(drill, offer.id)
            why = _unorderable_reason(offer, latest, as_of, needed_by, drill)
            reasons.append(
                f"报价{offer.id}（{order.units}单位/{order.cost}分）{why}"
            )
            continue
        candidates.append(order)
    if not candidates:
        return None, reasons
    return min(candidates, key=lambda item: (item.cost, item.units)), reasons


def _unorderable_reason(
    offer: Offer, latest: Offer | None, as_of: date, needed_by: date,
    drill: Drill,
) -> str:
    parts: list[str] = []
    if latest is not None and latest.id != offer.id and latest.valid_from <= as_of:
        parts.append(f"已被{latest.id}修订")
    if as_of < offer.valid_from:
        parts.append(f"未到生效日{offer.valid_from}")
    if as_of > offer.valid_until:
        parts.append(f"已于{offer.valid_until}过期")
    if offer.arrives_on > needed_by:
        parts.append(f"{offer.arrives_on}才到货，晚于开做日{needed_by}")
    return "、".join(parts)


@dataclass
class _PoolLot:
    """原料池中的一批存量。"""

    units: int
    source: str | None  # 采购编号；继承余量为来源采购编号
    refundable_units: int = 0
    refund_per_unit: int = 0
    inherited: bool = False


@dataclass
class MaterialLedger:
    """一个方案独立结算的原料总账。"""

    drill: Drill
    round_id: str
    opening: dict[str, list[_PoolLot]]
    balances: list[MaterialBalance]
    new_orders: list[LotOrder]
    blockers: list[str]
    late_materials: list[str]

    @property
    def sunk_paid(self) -> int:
        return sum(
            purchase.paid
            for purchase in self.drill.purchases
            if purchase.round == self.round_id
        )

    @property
    def new_spend(self) -> int:
        return sum(order.cost for order in self.new_orders)

    @property
    def total_refund(self) -> int:
        return sum(balance.refund for balance in self.balances)

    def leftover_stock(self) -> list[Stock]:
        """方案结算后可交接给下一轮的余量（含来源采购以便回查）。"""
        return [
            Stock(
                material=lot.material,
                units=lot.units,
                source_purchase=lot.source_purchase,
            )
            for balance in self.balances
            for lot in balance.leftover_lots
            if lot.units > 0
        ]


def _round_purchases(drill: Drill, round_id: str) -> list[Purchase]:
    return [
        purchase
        for purchase in drill.purchases
        if purchase.round == round_id
    ]


def build_ledger(
    drill: Drill,
    round_id: str,
    selected: list[Sample],
    as_of: date,
    opening_stock: list[Stock] | None = None,
) -> MaterialLedger:
    opening_stock = opening_stock or []
    opening: dict[str, list[_PoolLot]] = {}
    for stock in opening_stock:
        opening.setdefault(stock.material, []).append(
            _PoolLot(
                units=stock.units,
                source=stock.source_purchase,
                inherited=True,
            )
        )

    # 本轮已确认采购入池（即使方案完全不用，也保留在账上等待退货判定）。
    for purchase in _round_purchases(drill, round_id):
        material = drill.offer(purchase.offer).material
        opening.setdefault(material, []).append(
            _PoolLot(
                units=purchase.units,
                source=purchase.id,
                refundable_units=purchase.refundable_units,
                refund_per_unit=purchase.refund_per_unit,
            )
        )

    # 合并同料需求：一份采购供所有符合用料条件的样品共用。
    demand: dict[str, int] = {}
    earliest_start: dict[str, date] = {}
    for sample in selected:
        demand[sample.material] = demand.get(sample.material, 0) + sample.units
        if (
            sample.material not in earliest_start
            or sample.start < earliest_start[sample.material]
        ):
            earliest_start[sample.material] = sample.start

    # 没有需求但有存量的材料也要出账（付款不能因不再使用而消失）。
    materials = set(opening) | set(demand)
    balances: list[MaterialBalance] = []
    new_orders: list[LotOrder] = []
    blockers: list[str] = []
    late_materials: list[str] = []

    for material in sorted(materials):
        lots = list(opening.get(material, []))
        inherited_units = sum(lot.units for lot in lots if lot.inherited)
        existing_units = sum(lot.units for lot in lots)
        needed = demand.get(material, 0)

        order: LotOrder | None = None
        if needed > existing_units:
            shortfall = needed - existing_units
            order, reasons = cheapest_order(
                drill,
                material,
                shortfall,
                as_of,
                earliest_start[material],
            )
            if order is None:
                blockers.append(
                    f"材料{material}缺{shortfall}单位，当期无可下单报价："
                    + "；".join(reasons)
                )
                late_materials.append(material)
            else:
                new_orders.append(order)
                lots.append(_PoolLot(units=order.units, source=None))

        available = sum(lot.units for lot in lots)
        consumed = min(needed, available)

        # 先消耗继承余量，再按确认顺序消耗本轮采购，剩余才可退。
        remaining_need = consumed
        for lot in lots:
            take = min(remaining_need, lot.units)
            lot.units -= take
            remaining_need -= take

        refunded_units = 0
        refund = 0
        leftover_lots: list = []
        for lot in lots:
            if lot.units <= 0:
                continue
            if lot.inherited:
                # 上一轮交接的实物余量继续往下传，不再重复退款
                leftover_lots.append(
                    LeftoverLot(material, lot.units, lot.source)
                )
                continue
            refundable = min(lot.units, lot.refundable_units)
            refunded_units += refundable
            refund += refundable * lot.refund_per_unit
            leftover_lots.append(
                LeftoverLot(material, lot.units, lot.source)
            )
        leftover_source = next(
            (lot.source_purchase for lot in leftover_lots
             if lot.source_purchase is not None),
            None,
        )

        balances.append(
            MaterialBalance(
                material=material,
                needed=needed,
                inherited_units=inherited_units,
                existing_units=existing_units,
                new_order=order,
                consumed=consumed,
                leftover=available - consumed,
                leftover_purchase=leftover_source,
                refunded_units=refunded_units,
                refund=refund,
                leftover_lots=tuple(leftover_lots),
            )
        )

    return MaterialLedger(
        drill=drill,
        round_id=round_id,
        opening=opening,
        balances=balances,
        new_orders=new_orders,
        blockers=blockers,
        late_materials=late_materials,
    )
