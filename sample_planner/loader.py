"""把 data/drill.json 的原始行加载为领域对象。

只做字段映射与日期解析，不做业务计算；``null`` 原样保留为 ``None``。
"""
from __future__ import annotations

import json
from pathlib import Path

from .models import (
    CapacityEntry,
    Drill,
    Hold,
    Offer,
    Purchase,
    Round,
    Sample,
    parse_date,
)


def load_drill(path: str | Path) -> Drill:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    drill = Drill(
        rounds=[
            Round(
                id=row["id"],
                deadline=parse_date(row["deadline"]),
                budget=row["budget"],
                required_themes=tuple(row["required_themes"]),
            )
            for row in raw["rounds"]
        ],
        samples=[
            Sample(
                id=row["id"],
                design=row["design"],
                material=row["material"],
                units=row["units"],
                shop=row["shop"],
                hours=row["hours"],
                start=parse_date(row["start"]),
                deadline=parse_date(row["deadline"]),
                themes=tuple(row["themes"]),
                )
            for row in raw["samples"]
        ],
        offers=[
            Offer(
                id=row["id"],
                material=row["material"],
                minimum=row["minimum"],
                lot_step=row["lot_step"],
                lot_price=row["lot_price"],
                valid_from=parse_date(row["valid_from"]),
                valid_until=parse_date(row["valid_until"]),
                arrives_on=parse_date(row["arrives_on"]),
                revision_of=row["revision_of"],
            )
            for row in raw["offers"]
        ],
        capacity=[
            CapacityEntry(
                shop=row["shop"],
                on=parse_date(row["on"]),
                hours=row["hours"],
                known_on=parse_date(row["known_on"]),
            )
            for row in raw["capacity"]
        ],
        purchases=[
            Purchase(
                id=row["id"],
                round=row["round"],
                offer=row["offer"],
                units=row["units"],
                paid=row["paid"],
                refundable_units=row["refundable_units"],
                refund_per_unit=row["refund_per_unit"],
                committed_on=parse_date(row["committed_on"]),
            )
            for row in raw["purchases"]
        ],
        holds=[
            Hold(
                round=row["round"],
                sample=row["sample"],
                shop=row["shop"],
                on=parse_date(row["on"]),
                hours=row["hours"],
                strength=row["strength"],
            )
            for row in raw["holds"]
        ],
    )
    return drill
