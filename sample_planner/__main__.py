"""命令行入口：同一项目下整组样品方案的比较、诊断与续算。

示例：
  python -m sample_planner compare --round R1 --as-of 2026-08-05
  python -m sample_planner blockers --round R1 --as-of 2026-08-05
  python -m sample_planner handoff --round R1 --as-of 2026-08-05 \
      --confirm D11 D31 --refund C1
  python -m sample_planner compare --round R2 --as-of 2026-08-15 \
      --handoff-from R1 --prior-as-of 2026-08-05 \
      --confirm D11 D31 --refund C1
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .decisions import (
    Decision,
    apply_decisions,
    build_replan_request,
)
from .loader import load_drill
from .planner import PlanRequest, enumerate_plans, evaluate_plan, explain_blockers
from .report import (
    plan_to_dict,
    render_blockers,
    render_comparison,
    render_handoff,
)

DEFAULT_DATA = Path(__file__).resolve().parents[1] / "data" / "drill.json"


def _build_request(args, drill) -> PlanRequest:
    opening = ()
    locks = None
    if args.handoff_from:
        prior_as_of = args.prior_as_of or args.as_of
        prior_request = PlanRequest(
            round_id=args.handoff_from,
            as_of=prior_as_of,
        )
        prior_plan = evaluate_plan(drill, prior_request, tuple(args.confirm))
        decision = Decision(
            round_id=args.handoff_from,
            chosen_plan=prior_plan.sample_ids,
            confirmed_samples=tuple(args.confirm),
            refund_purchases=tuple(args.refund),
            decided_on=args.as_of,
        )
        handoff = apply_decisions(drill, prior_plan, decision)
        replan = build_replan_request(drill, handoff, args.round, args.as_of)
        opening = replan.opening_stock
        locks = replan.design_locks
    return PlanRequest(
        round_id=args.round,
        as_of=args.as_of,
        opening_stock=opening,
        design_locks=locks,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sample_planner",
        description="整组样品方案比较与续算（合成演练数据）",
    )
    parser.add_argument(
        "command",
        choices=("compare", "blockers", "handoff"),
        help="compare=比较全部组合；blockers=拆解堵点；handoff=确认并交接",
    )
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA)
    parser.add_argument("--round", required=True)
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        required=True,
        help="评估时点（知识截止日），容量/报价按此日已知事实",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读JSON")
    parser.add_argument("--handoff-from",
                        help="从指定轮次的确认结果继承余量与锁定")
    parser.add_argument("--confirm", nargs="*", default=[],
                        help="上一轮确认保留的样品（配合 --handoff-from）")
    parser.add_argument("--refund", nargs="*", default=[],
                        help="上一轮执行退货的采购编号")
    parser.add_argument("--prior-as-of", type=date.fromisoformat,
                        help="上一轮评估时点（配合 --handoff-from，默认同 --as-of）")
    args = parser.parse_args(argv)
    drill = load_drill(args.data)
    request = _build_request(args, drill)

    if args.command == "compare":
        plans = sorted(
            enumerate_plans(drill, request),
            key=lambda plan: (
                not plan.actionable,
                plan.net_cost,
                plan.gross_spend,
            ),
        )
        if args.json:
            print(json.dumps(
                [plan_to_dict(plan) for plan in plans],
                ensure_ascii=False, indent=2, default=str,
            ))
        else:
            print(render_comparison(drill, args.round, plans))
        return 0

    if args.command == "blockers":
        explanation = explain_blockers(drill, request)
        if args.json:
            print(json.dumps(explanation, ensure_ascii=False, indent=2))
        else:
            print(render_blockers(explanation))
        return 0

    # handoff：对指定确认结果落账并交接。
    if not args.confirm:
        parser.error("handoff 需要通过 --confirm 指定至少一个确认样品")
    plan = evaluate_plan(drill, request, tuple(args.confirm))
    decision = Decision(
        round_id=args.round,
        chosen_plan=plan.sample_ids,
        confirmed_samples=tuple(args.confirm),
        refund_purchases=tuple(args.refund),
        decided_on=args.as_of,
    )
    handoff = apply_decisions(drill, plan, decision)
    if args.json:
        payload = {
            "round": handoff.round_id,
            "evaluation": plan_to_dict(handoff.evaluation),
            "confirmed_samples": list(handoff.confirmed_samples),
            "opening_stock": [asdict(stock) for stock in handoff.opening_stock],
            "refunds": [
                {"purchase": pid, "amount": amount}
                for pid, amount in handoff.refunds
            ],
            "carried_purchases": list(handoff.carried_purchases),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print(render_handoff(handoff))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
