"""整组样品方案比较与续算引擎。

模块划分：
- models：资料行与方案结果的领域对象
- availability：报价有效期/交付时效、工坊容量（含后录缩短）
- materials：按步长报价的整批采购、共享原料池、退货回收
- planner：同一轮预算/截止日下的组合枚举、可行性诊断与方案评价
- decisions：采购确认、局部保留、重新安排、轮次交接
- report：采购与策展共用的同一张比较表
"""
from .models import (
    CapacityEntry,
    Hold,
    Offer,
    PlanEvaluation,
    Purchase,
    Round,
    Sample,
    Stock,
)
from .planner import PlanRequest, evaluate_plan, enumerate_plans, explain_blockers
from .decisions import Decision, PlanDiff, apply_decisions, compare_plans

__all__ = [
    "CapacityEntry",
    "Hold",
    "Offer",
    "PlanEvaluation",
    "Purchase",
    "Round",
    "Sample",
    "Stock",
    "PlanRequest",
    "evaluate_plan",
    "enumerate_plans",
    "explain_blockers",
    "Decision",
    "PlanDiff",
    "apply_decisions",
    "compare_plans",
]
