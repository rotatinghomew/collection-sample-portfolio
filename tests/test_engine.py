"""方案引擎的业务规则测试（合成演练数据）。"""
from datetime import date
from pathlib import Path
import unittest

from sample_planner.decisions import (
    Decision,
    apply_decisions,
    build_replan_request,
    compare_plans,
)
from sample_planner.loader import load_drill
from sample_planner.materials import lots_needed
from sample_planner.planner import (
    PlanRequest,
    actionable_plans,
    enumerate_plans,
    evaluate_plan,
    explain_blockers,
)

DATA = Path(__file__).resolve().parents[1] / "data" / "drill.json"
AUG1 = date(2026, 8, 1)
AUG5 = date(2026, 8, 5)
AUG15 = date(2026, 8, 15)


class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.drill = load_drill(DATA)

    def eval(self, ids, round_id="R1", as_of=AUG5, **kwargs):
        return evaluate_plan(
            self.drill,
            PlanRequest(round_id=round_id, as_of=as_of, **kwargs),
            ids,
        )

    def test_lot_pricing_is_whole_batch_not_unit_sum(self):
        # 素纸 lot_step=30、lot_price=9000：75 单位需 3 个步长=90 单位
        q2 = self.drill.offer("Q2")
        self.assertEqual(lots_needed(q2, 75), 3)
        plan = self.eval(("D12", "D41"))  # 40+35=75
        order = next(o for o in plan.new_orders if o.offer == "Q2")
        self.assertEqual((order.units, order.lots, order.cost), (90, 3, 27000))
        # 最小起订量约束：只要 10 单位也必须按 minimum=30 起订
        self.assertEqual(lots_needed(q2, 10), 1)

    def test_shared_material_pool_merged_across_samples(self):
        # D11+D21 同用压纹纸 40+35=75；已购 C1=60 共用，只补一次单
        plan = self.eval(("D11", "D21"), as_of=AUG1)
        balance = next(b for b in plan.balances if b.material == "压纹纸")
        self.assertEqual(balance.needed, 75)
        self.assertEqual(balance.existing_units, 60)
        self.assertIsNotNone(balance.new_order)
        # 缺口 15 但 minimum=60、步长 30，必须下 2 个步长=60 单位，
        # 不能只按缺口采购，也不能把两个样品的单位报价相加
        self.assertEqual(balance.new_order.units, 60)
        self.assertEqual(balance.new_order.cost, 83000)
        self.assertEqual(len(plan.new_orders), 1)
        # 超出合并需求的余量仍在账上：75 需求、120 可用，余 45
        self.assertEqual(balance.leftover, 45)

    def test_each_plan_recomputes_from_scratch_no_double_dipping(self):
        first = self.eval(("D11",), as_of=AUG1)
        second = self.eval(("D21",), as_of=AUG1)
        # 两个方案各自独立看到完整的 60 单位 C1，互不抽用
        self.assertEqual(
            next(b for b in first.balances if b.material == "压纹纸").existing_units,
            60,
        )
        self.assertEqual(
            next(b for b in second.balances if b.material == "压纹纸").existing_units,
            60,
        )

    def test_late_delivery_excluded_from_current_options(self):
        # 08-05 时点 Q1 已被 Q4 修订；Q4 08-12 才到货，晚于 D11 08-09 开做
        plan = self.eval(("D11", "D21"))
        self.assertIn("压纹纸", plan.late_materials)
        self.assertTrue(any("压纹纸" in b for b in plan.blockers))
        # 单独 D11 靠已购 C1 仍可行，无需新报价
        solo = self.eval(("D11",))
        self.assertTrue(solo.feasible)
        self.assertEqual(solo.new_orders, ())

    def test_capacity_cut_via_later_record_blocks_tentative_sample(self):
        # 08-01 已知 08-11 容量 6h，D21 可排；08-05 后录缩短为 3h，D21 被堵
        early = self.eval(("D11", "D21"), as_of=AUG1)
        self.assertTrue(
            all("容量" not in b for b in early.blockers),
            early.blockers,
        )
        late = self.eval(("D11", "D21"), as_of=AUG5)
        self.assertTrue(
            any("08-11" in b and "容量仅3工时" in b for b in late.blockers),
            late.blockers,
        )

    def test_confirmed_hold_cannot_be_dropped_tentative_released(self):
        plan = self.eval(("D31",))  # 放弃 confirmed 的 D11
        self.assertFalse(plan.feasible)
        self.assertTrue(
            any("confirmed占位D11" in b for b in plan.blockers)
        )
        kept = self.eval(("D11", "D31"))  # 放弃 tentative 的 D21
        self.assertIn("D21", kept.releases_tentative)
        # 反事实试算允许打破 confirmed，但必须显式标注
        counter = self.eval(
            ("D31",), allow_break_confirmed=True
        )
        self.assertTrue(counter.feasible)
        self.assertTrue(counter.breaks_confirmed)

    def test_substrate_switch_changes_theme_coverage(self):
        # D12 是 D1 的素纸备选版，主题为空，不能沿用 D11 的江岸覆盖
        d12 = self.eval(("D12",))
        self.assertEqual(d12.coverage, frozenset())
        self.assertEqual(d12.missing_themes, ("江岸", "器用"))
        self.assertFalse(d12.covers_themes)

    def test_one_version_per_design(self):
        clash = self.eval(("D11", "D12"))
        self.assertFalse(clash.feasible)
        self.assertTrue(any("每轮每设计最多一个版本" in b for b in clash.blockers))

    def test_design_lock_is_non_replaceable(self):
        locked = self.eval(
            ("D31",), design_locks={"D1": "D11"}
        )
        self.assertTrue(
            any("不可替换版本D11未保留" in b for b in locked.blockers)
        )

    def test_r1_actionable_plans_and_budget_views(self):
        plans = actionable_plans(self.drill, PlanRequest("R1", AUG5))
        ids = [p.sample_ids for p in plans]
        # 守住江岸+器用且无硬冲突的组合只有两个
        self.assertIn(("D11", "D31"), ids)
        self.assertIn(("D11", "D31", "D41"), ids)
        best = plans[0]
        self.assertEqual(best.sample_ids, ("D11", "D31"))
        # 毛支出：C1 已付 83000 + 陶片 28500 = 111500；
        # 压纹纸余 20，可退 20*900=18000；净占用 93500
        self.assertEqual(best.gross_spend, 111500)
        self.assertEqual(best.refunds, 18000)
        self.assertEqual(best.net_cost, 93500)
        self.assertEqual(best.headroom, 120000 - 93500)
        self.assertTrue(best.within_budget)

    def test_gross_over_budget_but_refund_rescues_plan(self):
        # 三款组合毛支出 129500 超预算，退款 18000 后净占用 111500 守住
        plan = self.eval(("D11", "D31", "D41"))
        self.assertGreater(plan.gross_spend, 120000)
        self.assertTrue(plan.within_budget)
        self.assertTrue(plan.feasible)
        # 素纸 35 单位起订 60（2 步长），余 25 不可退（Q2 无采购记录）
        paper = next(b for b in plan.balances if b.material == "素纸")
        self.assertEqual(paper.leftover, 25)
        self.assertEqual(paper.refund, 0)

    def test_paid_material_never_disappears_when_unused(self):
        # 方案完全不用压纹纸：C1 付款 83000 仍占账，可退 30*900=27000
        plan = self.eval(("D31", "D41"), allow_break_confirmed=True)
        emboss = next(b for b in plan.balances if b.material == "压纹纸")
        self.assertEqual(emboss.needed, 0)
        self.assertEqual(emboss.refund, 27000)
        # 付款没有被当成没发生：83000 沉没在毛支出里，再以退款冲减
        self.assertEqual(plan.gross_spend, 83000 + 28500 + 18000)
        self.assertEqual(plan.net_cost, plan.gross_spend - 27000)

    def test_blocker_explanation_separates_commitments_and_swaps(self):
        # R1 在 08-05 时点：已确认占位/采购与可换 tentative 被分开归因
        explanation = explain_blockers(
            self.drill, PlanRequest("R1", AUG5)
        )
        joined = " ".join(explanation["supply_and_capacity"])
        self.assertTrue(joined)  # 容量缩短与晚到报价都有记录
        self.assertTrue(
            any("confirmed占位D11" in item
                for item in explanation["confirmed_commitments"])
        )
        self.assertTrue(
            any("C1" in item and "不可回收" in item
                for item in explanation["confirmed_commitments"])
        )
        self.assertTrue(
            any("tentative" in item for item in explanation["swappable_parts"])
        )

    def test_r2_after_handoff_is_fully_supply_blocked(self):
        # R2 在 08-15 时点：所有报价均过期，带着 R1 锁定仍无组合可行
        plan = self.eval(("D11", "D31"))
        decision = Decision(
            round_id="R1",
            chosen_plan=plan.sample_ids,
            confirmed_samples=("D11",),
            refund_purchases=("C1",),
        )
        handoff = apply_decisions(self.drill, plan, decision)
        request = build_replan_request(self.drill, handoff, "R2", AUG15)
        replanned = evaluate_plan(self.drill, request, ("D11",))
        self.assertFalse(replanned.feasible)
        explanation = explain_blockers(self.drill, request)
        self.assertIn(
            "过期", " ".join(explanation["supply_and_capacity"])
        )
        self.assertTrue(explanation["swappable_parts"])

    def test_handoff_carries_stock_and_locks_for_next_round(self):
        plan = self.eval(("D11", "D31"))
        decision = Decision(
            round_id="R1",
            chosen_plan=plan.sample_ids,
            confirmed_samples=("D11",),
            refund_purchases=("C1",),
            decided_on=AUG5,
        )
        handoff = apply_decisions(self.drill, plan, decision)
        # 退掉 20 余单位中的 20（上限 30），回收 18000；无实物余量交接
        self.assertEqual(handoff.refunds, (("C1", 18000),))
        self.assertEqual(handoff.opening_stock, ())
        self.assertEqual(handoff.confirmed_samples, ("D11",))

        request = build_replan_request(self.drill, handoff, "R2", AUG15)
        self.assertEqual(request.design_locks, {"D1": "D11"})
        # 下一轮锁定 D11 但报价全过期且无余量 -> 明确的供应阻断
        replanned = evaluate_plan(self.drill, request, ("D11",))
        self.assertFalse(replanned.feasible)
        self.assertTrue(
            any("压纹纸" in b for b in replanned.blockers)
        )

    def test_handoff_without_refund_keeps_physical_stock(self):
        plan = self.eval(("D11", "D31", "D41"))
        decision = Decision(
            round_id="R1",
            chosen_plan=plan.sample_ids,
            confirmed_samples=("D11", "D31", "D41"),
            refund_purchases=(),
        )
        handoff = apply_decisions(self.drill, plan, decision)
        self.assertEqual(handoff.refunds, ())
        stock = {s.material: s for s in handoff.opening_stock}
        self.assertEqual(stock["压纹纸"].units, 20)
        self.assertEqual(stock["压纹纸"].source_purchase, "C1")
        # 素纸余量来自当轮新报价采购、无退货条款，只做实物交接
        self.assertEqual(stock["素纸"].units, 25)
        self.assertIsNone(stock["素纸"].source_purchase)

    def test_plan_diff_shows_margin_and_actions(self):
        baseline = self.eval(("D11", "D31"))
        bigger = self.eval(("D11", "D31", "D41"))
        diff = compare_plans(baseline, bigger)
        self.assertEqual(diff.added, ("D41",))
        self.assertEqual(diff.net_cost_delta, bigger.net_cost - baseline.net_cost)
        self.assertEqual(diff.headroom_delta, -diff.net_cost_delta)
        self.assertEqual(diff.coverage_lost, ())
        self.assertTrue(diff.candidate_feasible)

    def test_early_view_capacity_ok_but_budget_blocks_coverage(self):
        # 08-01 时点容量尚未缩短：D11+D21 排得进、覆盖达标，
        # 但压纹纸补单受 minimum=60 约束，净占用 166000 超预算 120000。
        plan = self.eval(("D11", "D21"), as_of=AUG1)
        self.assertTrue(plan.covers_themes)
        self.assertFalse(plan.within_budget)
        self.assertFalse(plan.feasible)
        self.assertTrue(any("预算" in b for b in plan.blockers))
        # 同口径诊断能指出是预算堵住，而未确认的 D21 仍可换组
        explanation = explain_blockers(
            self.drill, PlanRequest("R1", AUG1)
        )
        self.assertTrue(explanation["budget"])
        self.assertIn("D21", " ".join(explanation["swappable_parts"]))

    def test_replan_with_revised_quote_and_shortened_hours(self):        # 整批报价更正 + 工坊缩工时后的局部保留试算：
        # 只保留 confirmed D11，改选 D41 补江岸、D31 守器用
        all_plans = enumerate_plans(self.drill, PlanRequest("R1", AUG5))
        feasible = [p for p in all_plans if p.feasible]
        self.assertTrue(
            all("D11" in p.sample_ids for p in feasible),
            "可行方案必须保留 confirmed 的 D11",
        )
        # D21（tentative）在任何可行组合中都因容量/供应被排除，可换组
        self.assertTrue(
            all("D21" not in p.sample_ids for p in feasible)
        )


if __name__ == "__main__":
    unittest.main()
