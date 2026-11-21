"""采购与策展共用的同一张比较表：文本报告与机器可读 JSON。"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

from .decisions import PlanDiff, RoundHandoff
from .models import Drill, PlanEvaluation, date


def _yuan(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}元"


def _plan_line(plan: PlanEvaluation) -> str:
    ids = "+".join(plan.sample_ids) if plan.sample_ids else "（空组合）"
    status = "可行" if plan.feasible else "不可行"
    cover = "、".join(sorted(plan.coverage)) or "无覆盖"
    return (
        f"[{status}] {ids:<11} 净占用{_yuan(plan.net_cost):>9} "
        f"毛支出{_yuan(plan.gross_spend):>9} 退款{_yuan(plan.refunds):>8} "
        f"余量{_yuan(plan.headroom):>9} 主题[{cover}]"
    )


def render_comparison(
    drill: Drill,
    round_id: str,
    plans: Iterable[PlanEvaluation],
) -> str:
    rnd = drill.round(round_id)
    lines = [
        f"轮次 {rnd.id}：截止日 {rnd.deadline}，预算 {_yuan(rnd.budget)}，"
        f"必备主题 {'、'.join(rnd.required_themes)}",
        "=" * 72,
    ]
    for plan in plans:
        lines.append(_plan_line(plan))
        if not plan.feasible:
            for blocker in plan.blockers:
                lines.append(f"    ✗ {blocker}")
        if plan.missing_themes:
            lines.append(
                f"    ✗ 缺主题：{'、'.join(plan.missing_themes)}"
            )
        for balance in plan.balances:
            order = (
                f"，补单{balance.new_order.units}单位"
                f"/{_yuan(balance.new_order.cost)}（报价{balance.new_order.offer}）"
                if balance.new_order is not None
                else ""
            )
            lines.append(
                f"    · {balance.material}：需{balance.needed} "
                f"现有{balance.existing_units}（含继承{balance.inherited_units}）"
                f"{order} 余{balance.leftover} 退{balance.refunded_units}"
                f"单位/{_yuan(balance.refund)}"
            )
        if plan.releases_tentative:
            lines.append(
                f"    ↺ 释放tentative占位：{', '.join(plan.releases_tentative)}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_blockers(explanation: dict[str, list[str]]) -> str:
    titles = {
        "confirmed_commitments": "堵住可选范围的已确认承诺（不可单方撤）",
        "swappable_parts": "仍可换组的未确认部分",
        "supply_and_capacity": "供应与容量事实",
        "budget": "预算约束",
    }
    lines = ["可选范围的约束拆解（哪些承诺堵路、哪些未确认可换）：", "=" * 72]
    for key, title in titles.items():
        lines.append(f"## {title}")
        items = explanation.get(key) or ["（无）"]
        lines.extend(f"- {item}" for item in items)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_diff(diff: PlanDiff) -> str:
    lines = [
        f"方案对照：{'+'.join(diff.baseline) or '空'} -> "
        f"{'+'.join(diff.candidate) or '空'}",
        f"  保留：{', '.join(diff.kept) or '无'}；"
        f"加入：{', '.join(diff.added) or '无'}；"
        f"移除：{', '.join(diff.removed) or '无'}",
        f"  净占用变化 {_yuan(diff.net_cost_delta)}，"
        f"预算余量变化 {_yuan(diff.headroom_delta)}，"
        f"毛支出变化 {_yuan(diff.gross_spend_delta)}，"
        f"退款变化 {_yuan(diff.refund_delta)}",
        f"  主题覆盖：{'、'.join(sorted(diff.coverage_baseline)) or '无'}"
        f" -> {'、'.join(sorted(diff.coverage_candidate)) or '无'}"
        + (f"（丢失：{'、'.join(diff.coverage_lost)}）"
           if diff.coverage_lost else ""),
    ]
    if diff.new_orders_delta:
        lines.append("  采购变化：")
        lines.extend(f"    - {item}" for item in diff.new_orders_delta)
    if diff.releases_tentative:
        lines.append(
            f"  可行动作：释放tentative {', '.join(diff.releases_tentative)}"
        )
    if diff.breaks_confirmed:
        lines.append("  违反confirmed承诺（仅反事实试算）：")
        lines.extend(f"    ! {item}" for item in diff.breaks_confirmed)
    lines.append(f"  候选可行性：{'可行' if diff.candidate_feasible else '不可行'}")
    return "\n".join(lines) + "\n"


def render_handoff(handoff: RoundHandoff) -> str:
    lines = [
        f"轮次 {handoff.round_id} 交接状态",
        f"  选中方案：{'+'.join(handoff.evaluation.sample_ids)}",
        f"  已确认样品（下一轮锁定）："
        f"{', '.join(handoff.confirmed_samples) or '无'}",
        f"  交接余量："
        + (
            "；".join(
                f"{stock.material} {stock.units}单位"
                f"（来源{stock.source_purchase}）"
                for stock in handoff.opening_stock
            )
            or "无"
        ),
        f"  已执行退款："
        + (
            "；".join(f"{pid} {_yuan(amount)}" for pid, amount in handoff.refunds)
            or "无"
        ),
        f"  仍挂账采购：{', '.join(handoff.carried_purchases) or '无'}",
    ]
    return "\n".join(lines) + "\n"


def plan_to_dict(plan: PlanEvaluation) -> dict[str, Any]:
    data = asdict(plan)
    data["as_of"] = plan.as_of.isoformat()
    data["coverage"] = sorted(plan.coverage)
    data["schedule"] = [
        {"sample": sid, "shop": shop, "start": d.isoformat(), "hours": hours}
        for sid, shop, d, hours in plan.schedule
    ]
    return data
