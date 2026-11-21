"""报价时效与工坊容量。

两条关键规则：
1. 报价有有效期与交付时段；下单日必须在有效期内，且到货日不得晚于
   样品开做日（``sample.start``）。赶不上的物料不进入当期可行选项。
2. 容量按工坊和日期给出，后录记录反映新确认：同一工坊/日期只采用
   ``known_on`` 不晚于评估时点的最新一条。
另外，被后续修订替代的报价不能再用于新下单（历史采购不受影响）。
"""
from __future__ import annotations

from datetime import date

from .models import Drill, Offer, Sample


def capacity_hours(drill: Drill, shop: str, on: date, as_of: date) -> int | None:
    """截至 as_of 已知的某工坊某日容量；无记录返回 None。"""
    known = [
        entry
        for entry in drill.capacity
        if entry.shop == shop and entry.on == on and entry.known_on <= as_of
    ]
    if not known:
        return None
    return max(known, key=lambda entry: entry.known_on).hours


def superseded_offer_ids(drill: Drill) -> frozenset[str]:
    """所有存在修订链的报价（是否真正失效取决于修订是否已生效）。"""
    return frozenset(
        offer.revision_of for offer in drill.offers if offer.revision_of is not None
    )


def _revision_active(drill: Drill, offer_id: str, as_of: date) -> bool:
    revision = next(
        (row for row in drill.offers if row.revision_of == offer_id),
        None,
    )
    return revision is not None and revision.valid_from <= as_of


def is_orderable(
    offer: Offer,
    drill: Drill,
    as_of: date,
    needed_by: date | None = None,
) -> bool:
    """报价在评估时点能否用于新下单。

    - 尚未生效或已过期的报价不能用；
    - 已被"生效中的修订"替代的报价不能用（修订未生效前原报价仍有效）；
    - 需要赶某个开做日时，到货晚于该日的不能用。
    历史已付款采购不经过本判定，不受修订影响。
    """
    if offer.id in superseded_offer_ids(drill) and _revision_active(
        drill, offer.id, as_of
    ):
        return False
    if as_of < offer.valid_from or as_of > offer.valid_until:
        return False
    if needed_by is not None and offer.arrives_on > needed_by:
        return False
    return True


def usable_offers(
    drill: Drill, sample: Sample, as_of: date
) -> tuple[Offer, ...]:
    """该样品版本当期实际可下单的报价：同料、有效且能赶上开做日。"""
    return tuple(
        offer
        for offer in drill.offers
        if offer.material == sample.material
        and is_orderable(offer, drill, as_of, needed_by=sample.start)
    )


def latest_revision(drill: Drill, offer_id: str | None) -> Offer | None:
    """沿 revision_of 链找到最新修订（用于诊断报价更正）。"""
    if offer_id is None:
        return None
    current = drill.offer(offer_id)
    while True:
        nxt = next(
            (row for row in drill.offers if row.revision_of == current.id),
            None,
        )
        if nxt is None:
            return current
        current = nxt
