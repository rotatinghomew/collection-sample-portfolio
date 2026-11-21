# 文创样品预算演练

经营演练资料均为合成数据，不涉及真实参赛者、顾客或合作方。 `data/drill.json` 为 UTF-8 JSON；顶层键为来源表名，值为行数组。日期按当地工作日历，编号是代号而非名称。 金额使用整数分；`null` 表示未取得该事实，不等于零。

每个 `design` 在本轮最多使用一个 `samples.id`。`start` 是工坊所需日期；报价的 `minimum` 是最小总量，`lot_step` 为下单步长，`lot_price` 是每个步长的报价。容量按工坊和日期给出；后录记录反映新确认。已付款采购的退回上限和单价分别见 `refundable_units` 与 `refund_per_unit`。

## 资料预检

使用 Python 3.11 或更高版本，无第三方依赖。 在项目根目录运行 `python3 -m unittest discover -s tests -v` 核对演练资料的表结构、事实关联及业务场景所需的原始记录。
