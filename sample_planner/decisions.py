"""采购确认、局部保留、重新安排与轮次交接。

方案出来后采购只确认其中一部分：confirmed 样品/采购进入下一轮不可动账，
未确认款仍允许换组。整批报价更正、工坊缩短工时时，可以只保留已确认
部分重新试算。已付款采购不随组合变化而消失，可退部分按退货条件回收，
余量与新承诺一起交接，下一轮不从空白预算开始。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .materials import build_ledger
from .models import Drill, PlanEvaluation, Stock
from .planner import PlanRequest


@dataclass(frozen=True)
class Decision:
    """对某个方案的采购确认结果。"""

    round_id: str
    chosen_plan: tuple[str, ...]
    # 确认保留的样品（其余视为未确认、下一轮可换组）
    confirmed_samples: tuple[str, ...]
    # 确认执行退款的采购；未列出的已付款采购保留实物余量
    refund_purchases: tuple[str, ...] = ()
    decided_on: date | None = None
    note: str = ""


@dataclass(frozen=True)
class RoundHandoff:
    """一轮结束后交给后续分析的完整状态，可从方案回查原料与确认事项。"""

    round_id: str
    decision: Decision
    evaluation: PlanEvaluation
    # 进入下一轮开账的原料余量（带来源采购编号）
    opening_stock: tuple[Stock, ...]
    # 已确认、下一轮不可单方撤的样品
    confirmed_samples: tuple[str, ...]
    # 已执行退款的采购与回收金额
    refunds: tuple[tuple[str, int], ...]
    # 仍挂在账上的已付款采购（含不可退沉没部分）
    carried_purchases: tuple[str, ...]


@dataclass(frozen=True)
class PlanDiff:
    """两个方案改动前后的余量与可行动作对照。"""

    baseline: tuple[str, ...]
    candidate: tuple[str, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    kept: tuple[str, ...]
    net_cost_delta: int
    headroom_delta: int
    gross_spend_delta: int
    refund_delta: int
    coverage_baseline: frozenset[str]
    coverage_candidate: frozenset[str]
    coverage_lost: tuple[str, ...]
    new_orders_delta: tuple[str, ...]
    releases_tentative: tuple[str, ...]
    breaks_confirmed: tuple[str, ...]
    candidate_feasible: bool


def apply_decisions(
    drill: Drill,
    evaluation: PlanEvaluation,
    decision: Decision,
) -> RoundHandoff:
    """把确认结果落到账目，产出下一轮开账状态。"""
    if tuple(decision.chosen_plan) != evaluation.sample_ids:
        raise ValueError("确认决定必须对应被评价的方案")
    unknown = set(decision.confirmed_samples) - set(evaluation.sample_ids)
    if unknown:
        raise ValueError(f"被确认的样品不在方案内：{sorted(unknown)}")

    selected = [drill.sample(sid) for sid in evaluation.sample_ids]
    ledger = build_ledger(
        drill,
        decision.round_id,
        selected,
        evaluation.as_of,
    )

    refund_records: list[tuple[str, int]] = []
    refund_ids = set(decision.refund_purchases)
    # 按批次处理余量：退款采购的可退部分回收，其余实物（含新报价补单）
    # 带着来源交接，下一轮不从空白预算开始。
    stock_by_material: dict[str, list[Stock]] = {}
    for balance in ledger.balances:
        for lot in balance.leftover_lots:
            units = lot.units
            if lot.source_purchase is not None and lot.source_purchase in refund_ids:
                purchase = next(
                    item for item in drill.purchases
                    if item.id == lot.source_purchase
                )
                refundable = min(units, purchase.refundable_units)
                refund_records.append(
                    (purchase.id, refundable * purchase.refund_per_unit)
                )
                units -= refundable
            if units > 0:
                stock_by_material.setdefault(lot.material, []).append(
                    Stock(
                        material=lot.material,
                        units=units,
                        source_purchase=lot.source_purchase,
                    )
                )

    opening_stock = tuple(
        stock
        for stocks in stock_by_material.values()
        for stock in stocks
    )
    carried = tuple(
        purchase.id
        for purchase in drill.purchases
        if purchase.round == decision.round_id
        and purchase.id not in refund_ids
    )
    return RoundHandoff(
        round_id=decision.round_id,
        decision=decision,
        evaluation=evaluation,
        opening_stock=opening_stock,
        confirmed_samples=tuple(decision.confirmed_samples),
        refunds=tuple(refund_records),
        carried_purchases=carried,
    )


def build_replan_request(
    drill: Drill,
    handoff: RoundHandoff,
    next_round_id: str,
    as_of: date,
) -> PlanRequest:
    """带资料上下文的重排请求：确认款成为不可替换锁定，余量带入开账。"""
    locks = {
        drill.sample(sid).design: sid
        for sid in handoff.confirmed_samples
    }
    return PlanRequest(
        round_id=next_round_id,
        as_of=as_of,
        opening_stock=handoff.opening_stock,
        design_locks=locks,
    )


def compare_plans(
    baseline: PlanEvaluation, candidate: PlanEvaluation
) -> PlanDiff:
    """对照两个方案的成本、覆盖、采购与可行动作，支持局部保留试算。"""
    base = set(baseline.sample_ids)
    cand = set(candidate.sample_ids)
    base_orders = {order.material: order for order in baseline.new_orders}
    cand_orders = {order.material: order for order in candidate.new_orders}
    order_delta = tuple(
        f"{material}: {base_orders.get(material).units if material in base_orders else 0}"
        f"->{order.units}单位（{order.cost}分，报价{order.offer}）"
        for material, order in sorted(cand_orders.items())
        if base_orders.get(material) != order
    )
    coverage_lost = tuple(
        sorted(baseline.coverage - candidate.coverage)
    )
    return PlanDiff(
        baseline=baseline.sample_ids,
        candidate=candidate.sample_ids,
        added=tuple(sorted(cand - base)),
        removed=tuple(sorted(base - cand)),
        kept=tuple(sorted(base & cand)),
        net_cost_delta=candidate.net_cost - baseline.net_cost,
        headroom_delta=candidate.headroom - baseline.headroom,
        gross_spend_delta=candidate.gross_spend - baseline.gross_spend,
        refund_delta=candidate.refunds - baseline.refunds,
        coverage_baseline=baseline.coverage,
        coverage_candidate=candidate.coverage,
        coverage_lost=coverage_lost,
        new_orders_delta=order_delta,
        releases_tentative=candidate.releases_tentative,
        breaks_confirmed=candidate.breaks_confirmed,
        candidate_feasible=candidate.feasible,
    )
