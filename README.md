# 文创样品预算演练

经营演练资料均为合成数据，不涉及真实参赛者、顾客或合作方。 `data/drill.json` 为 UTF-8 JSON；顶层键为来源表名，值为行数组。日期按当地工作日历，编号是代号而非名称。 金额使用整数分；`null` 表示未取得该事实，不等于零。

每个 `design` 在本轮最多使用一个 `samples.id`。`start` 是工坊所需日期；报价的 `minimum` 是最小总量，`lot_step` 为下单步长，`lot_price` 是每个步长的报价。容量按工坊和日期给出；后录记录反映新确认。已付款采购的退回上限和单价分别见 `refundable_units` 与 `refund_per_unit`。

## 资料预检

使用 Python 3.11 或更高版本，无第三方依赖。 在项目根目录运行 `python3 -m unittest discover -s tests -v` 核对演练资料的表结构、事实关联及业务场景所需的原始记录。

## 方案引擎 `sample_planner`

采购与策展共用同一项目、同一张比较表，不再各做一张。引擎把来源表加载为领域对象后，在**同一轮预算与试卖截止日**下枚举"每设计至多一个版本"的全部样品组合，并对每个组合给出整体成本、主题覆盖、无法按时出现的货与取舍。

口径与规则：

- **整批报价，不做单位价相加**：补单量先满足 `minimum`，再按 `lot_step` 向上取整，成本为步数 × `lot_price`；超量部分仍在采购账上，不能免费忽略。
- **共享原料池**：同一材料先合并所有入选样品的总需求再决定补单，一笔采购供所有符合用料条件的样品共用；每个方案独立从零结算，余量不会被重复抽给下一个方案。
- **报价时效与交付**：下单日须落在有效期内；到货晚于样品 `start` 的报价不进入当期可行选项；被"已生效修订"替代的报价不能再用于新下单（修订未生效前原报价仍有效），历史已付款采购不受影响。
- **工坊容量**：同一工坊/日期只采用评估时点（`--as-of`）已知的最新一条容量记录，后录缩短即时生效；`confirmed` 占位不可单方撤，`tentative` 占位随换组释放。
- **主题覆盖按所选版本实际承诺计算**：换基材（如 D11 压纹纸 → D12 素纸）可能丢失主题，不沿用原设计评价。
- **预算三口径**：毛支出（含已付款，付款事实不随组合变化消失）、按退货条件回收的退款、净占用与预算余量。
- **续算与交接**：采购只确认部分方案；未确认款可换组。交接对象携带余量（含来源采购编号）、已确认锁定、退款记录与挂账采购，下一轮带着余量和锁定重排，而不是从空白预算开始。

### 命令行

```bash
# 枚举并比较 R1 全部组合（截至 2026-08-05 的已知事实）
python3 -m sample_planner compare --round R1 --as-of 2026-08-05

# 拆解堵点：哪些 confirmed 承诺堵路、哪些未确认款可换、供应/容量事实
python3 -m sample_planner blockers --round R1 --as-of 2026-08-05

# 采购确认 D11、D31，并对 C1 的可退余量执行退货，产出交接状态
python3 -m sample_planner handoff --round R1 --as-of 2026-08-05 \
    --confirm D11 D31 --refund C1

# 带着 R1 的余量与确认锁定重排 R2（报价已全部过期时会明确指出供应阻断）
python3 -m sample_planner compare --round R2 --as-of 2026-08-15 \
    --handoff-from R1 --prior-as-of 2026-08-05 \
    --confirm D11 D31 --refund C1
```

任一命令加 `--json` 输出机器可读结果，供后续分析直接消费选中的新承诺。

### 库 API

```python
from datetime import date
from sample_planner.loader import load_drill
from sample_planner.planner import PlanRequest, actionable_plans, evaluate_plan
from sample_planner.decisions import Decision, apply_decisions, compare_plans

drill = load_drill("data/drill.json")
request = PlanRequest(round_id="R1", as_of=date(2026, 8, 5))
plans = actionable_plans(drill, request)          # 可承诺组合，按净占用排序
plan = evaluate_plan(drill, request, ("D11", "D31"))
diff = compare_plans(plans[0], plans[-1])         # 改动前后的余量与可行动作
handoff = apply_decisions(
    drill, plan,
    Decision("R1", plan.sample_ids, confirmed_samples=("D11",),
             refund_purchases=("C1",)),
)                                                 # 交接给下一轮
```

`tests/test_engine.py` 对每条业务规则（步长起订、共享池不重复抽用、晚到剔除、容量缩短、不可替换锁定、换基材丢主题、退款回收、交接续算等）都有对应测试。
