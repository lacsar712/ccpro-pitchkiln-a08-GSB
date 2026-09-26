"""脂液拼配业务规则。

开单核验、结案核验、开灶挂批核验同源：三处都建立在同一个
原语「未结案拼配单（closedAt 为空）锁定其明细来脂批」之上——

- ``lot_lock_ticket``：判定一个来脂批当前被哪张未结案单锁定；
- ``validate_blend_lines``：开单与结案共用的整单明细核验；
- ``assert_lot_free_for_hearth``：开灶挂批核验，复用 ``lot_lock_ticket``。
"""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.utils import timezone


def lot_lock_ticket(lot, exclude_ticket=None):
    """返回该来脂批当前所在的未结案拼配单；未被锁定则返回 None。

    这是「拼配锁定」的唯一判定来源：结案核验与开灶挂批核验都经由此函数。
    """
    from apps.kiln.models import BlendTicketLine

    qs = BlendTicketLine.objects.filter(
        resinLot=lot,
        ticket__closedAt__isnull=True,
    ).select_related("ticket")
    if exclude_ticket is not None:
        qs = qs.exclude(ticket=exclude_ticket)
    line = qs.first()
    return line.ticket if line else None


def locked_lot_ids():
    """所有被未结案拼配单锁定的来脂批 id 集合。"""
    from apps.kiln.models import BlendTicketLine

    return set(
        BlendTicketLine.objects.filter(
            ticket__closedAt__isnull=True
        ).values_list("resinLot_id", flat=True)
    )


def lock_map(lots=None):
    """``{resinLot_id: 未结案拼配单}`` 映射，供来脂批卡片标注拼配锁定。"""
    from apps.kiln.models import BlendTicketLine

    qs = BlendTicketLine.objects.filter(
        ticket__closedAt__isnull=True
    ).select_related("ticket")
    if lots is not None:
        qs = qs.filter(resinLot__in=lots)
    return {line.resinLot_id: line.ticket for line in qs}


def assert_lot_free_for_hearth(lot):
    """开灶挂批核验：批被未结案拼配单锁定时，禁止开新值守。"""
    ticket = lot_lock_ticket(lot)
    if ticket is not None:
        raise ValidationError(
            f"来脂批 {lot.lotCode} 挂在未结案拼配单 #{ticket.pk}，"
            "须主管结案后方可挂灶开值守。"
        )


def validate_blend_lines(*, planned_total, lines, exclude_ticket=None):
    """开单与结案共用的明细核验。

    lines 为 ``[(ResinLot, countedKg), ...]``。规则：
    - 明细至少一行；
    - 单批计入必须大于 0，且不得大于该批到货千克；
    - 同一来脂批不得在单内重复；
    - 同一来脂批不得已在另一张未结案单里；
    - 计入千克合计必须精确等于计划总重。

    核验不过时抛出带全部原因的 ``ValidationError``。
    """
    errors = []
    if not lines:
        errors.append("拼配明细至少需要一行。")

    seen = set()
    total = Decimal("0")
    for lot, kg in lines:
        total += kg
        if kg is None or kg <= 0:
            errors.append(f"{lot.lotCode}：计入千克必须大于 0。")
        elif kg > lot.arrivalKg:
            errors.append(
                f"{lot.lotCode}：计入 {kg}kg 大于该批到货 {lot.arrivalKg}kg。"
            )
        if lot.pk in seen:
            errors.append(f"{lot.lotCode}：同一批在明细中重复。")
        seen.add(lot.pk)
        other = lot_lock_ticket(lot, exclude_ticket=exclude_ticket)
        if other is not None:
            errors.append(
                f"{lot.lotCode}：已在未结案拼配单 #{other.pk} 内，"
                "不得再进另一张未结案单。"
            )

    if planned_total is not None and lines and total != planned_total:
        errors.append(
            f"计入千克合计 {total}kg 必须精确等于计划总重 {planned_total}kg。"
        )

    if errors:
        raise ValidationError(errors)


def assert_ticket_closeable(ticket):
    """结案核验：与开单同源地复核整张单的明细。"""
    if ticket.closedAt is not None:
        raise ValidationError(f"拼配单 #{ticket.pk} 已结案，无需重复结案。")
    lines = [
        (line.resinLot, line.countedKg)
        for line in ticket.lines.select_related("resinLot")
    ]
    validate_blend_lines(
        planned_total=ticket.plannedTotalKg,
        lines=lines,
        exclude_ticket=ticket,
    )


def close_ticket(ticket, user):
    """主管结案：核验通过才写入结案时刻，明细批随之解锁。"""
    if not getattr(user, "is_superuser", False):
        raise ValidationError("只有主管才能结案拼配单。")
    assert_ticket_closeable(ticket)
    ticket.closedAt = timezone.now()
    ticket.save(update_fields=["closedAt"])
    return ticket
