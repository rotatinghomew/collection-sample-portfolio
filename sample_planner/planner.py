"""同一轮预算与试卖截止日下的样品组合评价与枚举。

评价一个组合时依次检查：
1. 每个 design 最多一个样品版本；策展锁定的不可替换款必须保留；
2. 工坊容量（后录缩短按评估时点生效），confirmed 占位不可单方撤，
   tentative 占位在换组时释放；
3. 样品截止日不晚于本轮试卖截止日；
4. 原料池：共享采购合并结算，晚到/过期报价不算当期可行选项；
5. 主题覆盖按所选版本实际承诺的主题计算（换基材会改变覆盖）；
6. 预算同时给出口径：毛支出、退款回收、净占用与余量。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import product

from .availability import capacity_hours
from .materials import build_ledger
from .models import Drill, PlanEvaluation, Sample, Stock


@dataclass(frozen=True)
class PlanRequest:
    round_id: str
    as_of: date
    opening_stock: tuple[Stock, ...] = ()
    # design -> 必须保留的样品版本（策展的不可替换条件）
    design_locks: dict[str, str] | None = None
    # 允许违反 confirmed 占位做反事实试算时置 True（默认不允许）
    allow_break_confirmed: bool = False


def _holds_for(drill: Drill, round_id: str):
    confirmed = [hold for hold in drill.holds
                 if hold.round == round_id and hold.confirmed]
    tentative = [hold for hold in drill.holds
                 if hold.round == round_id and not hold.confirmed]
    return confirmed, tentative


def evaluate_plan(
    drill: Drill,
    request: PlanRequest,
    sample_ids: tuple[str, ...] | list[str],
) -> PlanEvaluation:
    rnd = drill.round(request.round_id)
    blockers: list[str] = []
    selected: list[Sample] = []
    unknown = [sid for sid in sample_ids if not any(
        s.id == sid for s in drill.samples)]
    if unknown:
        blockers.append(f"样品编号不存在：{', '.join(unknown)}")
    selected = [drill.sample(sid) for sid in sample_ids if not any(
        s.id == sid for s in unknown)]

    # 规则1：同一 design 最多一个版本。
    seen_designs: dict[str, str] = {}
    for sample in selected:
        if sample.design in seen_designs:
            blockers.append(
                f"设计{sample.design}同时选了{seen_designs[sample.design]}"
                f"与{sample.id}，每轮每设计最多一个版本"
            )
        seen_designs[sample.design] = sample.id

    # 策展的不可替换条件。
    locks = request.design_locks or {}
    for design, locked_id in locks.items():
        if seen_designs.get(design) != locked_id:
            blockers.append(
                f"设计{design}的不可替换版本{locked_id}未保留"
            )

    # 规则3：试卖截止日。
    for sample in selected:
        if sample.deadline > rnd.deadline:
            blockers.append(
                f"样品{sample.id}完成日{sample.deadline}晚于本轮"
                f"截止日{rnd.deadline}"
            )

    # 规则2：工坊容量与占位。
    confirmed_holds, tentative_holds = _holds_for(drill, request.round_id)
    selected_ids = {sample.id for sample in selected}

    breaks_confirmed: list[str] = []
    for hold in confirmed_holds:
        if hold.sample not in selected_ids:
            message = (
                f"confirmed占位{hold.sample}（{hold.shop} {hold.on} "
                f"{hold.hours}工时）被放弃，该承诺不可单方撤"
            )
            if request.allow_break_confirmed:
                breaks_confirmed.append(message)
            else:
                blockers.append(message)

    # 占用 = 选中样品自身工时 + 未被选中样品的 confirmed 占位；
    # 未选中样品的 tentative 占位随换组释放。
    usage: dict[tuple[str, date], int] = {}
    for sample in selected:
        key = (sample.shop, sample.start)
        usage[key] = usage.get(key, 0) + sample.hours
    for hold in confirmed_holds:
        if hold.sample not in selected_ids:
            key = (hold.shop, hold.on)
            usage[key] = usage.get(key, 0) + hold.hours

    releases_tentative = [
        hold.sample
        for hold in tentative_holds
        if hold.sample not in selected_ids
    ]

    for (shop, on_date), hours in sorted(usage.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        cap = capacity_hours(drill, shop, on_date, request.as_of)
        if cap is None:
            blockers.append(
                f"{shop}在{on_date}没有已知容量记录，无法承诺{hours}工时"
            )
        elif hours > cap:
            blockers.append(
                f"{shop}在{on_date}需{hours}工时，"
                f"截至{request.as_of}容量仅{cap}工时"
            )

    # 规则4：原料池。
    ledger = build_ledger(
        drill,
        request.round_id,
        selected,
        request.as_of,
        list(request.opening_stock),
    )
    blockers.extend(ledger.blockers)

    # 规则5：主题覆盖按所选版本实际主题，不沿用原设计评价。
    coverage = frozenset(
        theme for sample in selected for theme in sample.themes
    )
    missing = tuple(
        theme for theme in rnd.required_themes if theme not in coverage
    )
    covers = not missing

    # 规则6：预算口径。已付款始终占预算；退款按退货条件冲减。
    gross = ledger.sunk_paid + ledger.new_spend
    refunds = ledger.total_refund
    net = gross - refunds
    within = net <= rnd.budget
    if not within:
        blockers.append(
            f"净占用{net}分超出本轮预算{rnd.budget}分"
            f"（毛支出{gross}，可退{refunds}）"
        )

    schedule = tuple(
        sorted(
            (
                (sample.id, sample.shop, sample.start, sample.hours)
                for sample in selected
            ),
            key=lambda row: (row[2], row[0]),
        )
    )

    return PlanEvaluation(
        round_id=request.round_id,
        sample_ids=tuple(sample.id for sample in selected),
        as_of=request.as_of,
        feasible=not blockers,
        covers_themes=covers,
        within_budget=within,
        gross_spend=gross,
        refunds=refunds,
        net_cost=net,
        headroom=rnd.budget - net,
        coverage=coverage,
        missing_themes=missing,
        balances=tuple(ledger.balances),
        new_orders=tuple(ledger.new_orders),
        schedule=schedule,
        blockers=tuple(blockers),
        breaks_confirmed=tuple(breaks_confirmed),
        releases_tentative=tuple(releases_tentative),
        late_materials=tuple(ledger.late_materials),
    )


def enumerate_plans(
    drill: Drill, request: PlanRequest
) -> list[PlanEvaluation]:
    """枚举每设计至多一个版本的全部组合（含空组合），并完成评价。"""
    by_design: dict[str, list[Sample]] = {}
    for sample in drill.samples:
        by_design.setdefault(sample.design, []).append(sample)

    # 每个设计的选项：不选，或选其中一个版本。
    option_lists = [
        [None, *versions] for versions in by_design.values()
    ]
    results: list[PlanEvaluation] = []
    for choice in product(*option_lists):
        ids = tuple(sample.id for sample in choice if sample is not None)
        results.append(evaluate_plan(drill, request, ids))
    return results


def actionable_plans(
    drill: Drill, request: PlanRequest
) -> list[PlanEvaluation]:
    """无硬冲突且守住主题覆盖的组合，按净占用从低到高排序。"""
    plans = [
        plan
        for plan in enumerate_plans(drill, request)
        if plan.actionable
    ]
    return sorted(plans, key=lambda plan: (plan.net_cost, plan.gross_spend))


def explain_blockers(
    drill: Drill, request: PlanRequest
) -> dict[str, list[str]]:
    """没有组合能同时守住覆盖和预算时，区分堵住范围的承诺类型。

    返回三类说明：confirmed 承诺（不可动）、tentative/未确认款（可换组）、
    供应与容量事实（报价时效、到货、容量缩短）。
    """
    plans = enumerate_plans(drill, request)
    rnd = drill.round(request.round_id)
    out: dict[str, list[str]] = {
        "confirmed_commitments": [],
        "swappable_parts": [],
        "supply_and_capacity": [],
        "budget": [],
    }

    confirmed_holds, tentative_holds = _holds_for(drill, request.round_id)
    for hold in confirmed_holds:
        out["confirmed_commitments"].append(
            f"confirmed占位{hold.sample}：{hold.shop} {hold.on} "
            f"{hold.hours}工时，不可单方撤"
        )
    for purchase in drill.purchases:
        if purchase.round != request.round_id:
            continue
        stuck = purchase.paid - purchase.max_refund
        out["confirmed_commitments"].append(
            f"已付款采购{purchase.id}（报价{purchase.offer}，"
            f"{purchase.units}单位，付{purchase.paid}分）："
            f"最多退{purchase.max_refund}分，{stuck}分不可回收"
        )
    for hold in tentative_holds:
        out["swappable_parts"].append(
            f"tentative占位{hold.sample}：{hold.shop} {hold.on}，可换组释放"
        )

    seen: set[str] = set()
    material_reasons: dict[str, str] = {}
    for plan in plans:
        for blocker in plan.blockers:
            if blocker in seen:
                continue
            seen.add(blocker)
            if "预算" in blocker or "超出" in blocker:
                out["budget"].append(blocker)
            elif blocker.startswith("材料") and "缺" in blocker:
                # 同一材料的不同缺口只保留一条诊断，避免重复刷屏
                material = blocker.split("缺", 1)[0].removeprefix("材料")
                material_reasons.setdefault(material, blocker)
            elif "报价" in blocker or "容量" in blocker:
                out["supply_and_capacity"].append(blocker)
            elif "不可替换" in blocker:
                out["confirmed_commitments"].append(blocker)
    out["supply_and_capacity"].extend(
        material_reasons[key] for key in sorted(material_reasons)
    )

    # 哪些未确认款可换：给出当前能覆盖必备主题的候选版本。
    required = set(rnd.required_themes)
    for sample in drill.samples:
        if required & sample.covers:
            out["swappable_parts"].append(
                f"样品{sample.id}（设计{sample.design}，"
                f"材料{sample.material}）可承诺主题"
                f"{'、'.join(sorted(sample.covers)) or '无'}"
            )
    return out
