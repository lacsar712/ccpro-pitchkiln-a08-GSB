"""脂液拼配业务规则。

开单、结案、开灶挂批三处核验同源：都以「未结案拼配单的明细行」
（`_unclosed_line_qs`）为唯一事实来源 ——

- 开单：`open_blend_ticket` → `validate_blend_lines`
- 结案：`close_blend_ticket` → `validate_blend_lines`（复核，防止结案前数据被改）
- 开灶挂批：`assert_lot_ready_for_run`（开新值守前的来脂批锁定检查）
- 卡片角标：`lot_blend_status_map`
"""
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone


def _unclosed_line_qs():
    """未结案拼配单的所有明细行 —— 锁定判定的唯一来源。"""
    from apps.kiln.models import BlendTicketLine

    return BlendTicketLine.objects.filter(ticket__closedAt__isnull=True)


def locked_lot_ids(exclude_ticket_id=None):
    """被未结案拼配单锁定的来脂批 id 集合。"""
    qs = _unclosed_line_qs()
    if exclude_ticket_id is not None:
        qs = qs.exclude(ticket_id=exclude_ticket_id)
    return set(qs.values_list("resinLot_id", flat=True))


def locking_ticket_for_lot(lot):
    """返回锁定该来脂批的未结案拼配单；无则 None。"""
    line = (
        _unclosed_line_qs()
        .filter(resinLot=lot)
        .select_related("ticket")
        .first()
    )
    return line.ticket if line else None


def assert_lot_ready_for_run(lot):
    """开灶挂批核验：来脂批被未结案拼配单锁定时，禁止开新值守。"""
    ticket = locking_ticket_for_lot(lot)
    if ticket is not None:
        raise ValidationError(
            f"来脂批 {lot.lotCode} 已进未结案拼配单 #{ticket.pk}，"
            "主管结案前禁止挂灶开值守。"
        )


def lot_blend_status_map(lots):
    """
    来脂批卡片角标数据：{lot_id: (state, ticket)}。
    state: "locked" 未结案锁定 / "released" 最近一张拼配单已结案放行。
    """
    from apps.kiln.models import BlendTicketLine

    lot_ids = [lot.pk for lot in lots]
    lines = (
        BlendTicketLine.objects.filter(resinLot_id__in=lot_ids)
        .select_related("ticket")
        .order_by("-ticket__openedAt", "-ticket_id", "-id")
    )
    status = {}
    for line in lines:
        current = status.get(line.resinLot_id)
        if current and current[0] == "locked":
            continue
        if line.ticket.closedAt is None:
            status[line.resinLot_id] = ("locked", line.ticket)
        elif current is None:
            status[line.resinLot_id] = ("released", line.ticket)
    return status


def validate_blend_lines(*, planned_total_kg, lines, exclude_ticket_id=None):
    """
    开单与结案共用的明细核验（同源）：

    1. 至少一行明细；
    2. 每行计入千克 > 0，且不得大于该批到货千克；
    3. 同一来脂批在本单内不得重复；
    4. 计入千克合计必须精确等于计划总重；
    5. 同一来脂批不得已在其他未结案拼配单中。

    lines: [(resin_lot, counted_kg), ...]
    """
    if not lines:
        raise ValidationError("拼配单至少需要一行明细。")

    seen = set()
    total = Decimal("0")
    for lot, kg in lines:
        if kg is None or kg <= 0:
            raise ValidationError(f"来脂批 {lot.lotCode} 的计入千克必须大于 0。")
        if kg > lot.arrivalKg:
            raise ValidationError(
                f"来脂批 {lot.lotCode} 计入 {kg} kg 超过到货 {lot.arrivalKg} kg。"
            )
        if lot.pk in seen:
            raise ValidationError(f"来脂批 {lot.lotCode} 在本单明细中重复。")
        seen.add(lot.pk)
        total += kg

    if total != planned_total_kg:
        raise ValidationError(
            f"计入千克合计 {total} kg 必须精确等于计划总重 {planned_total_kg} kg。"
        )

    conflict = seen & locked_lot_ids(exclude_ticket_id=exclude_ticket_id)
    if conflict:
        from apps.kiln.models import ResinLot

        codes = ResinLot.objects.filter(pk__in=conflict).values_list(
            "lotCode", flat=True
        )
        raise ValidationError(
            "以下来脂批已在其他未结案拼配单中，结案前不得重复开单："
            + "、".join(codes)
        )


@transaction.atomic
def open_blend_ticket(*, blend_date, target_grade, planned_total_kg, opened_by, lines):
    """开单：先过同源核验，再事务写入单头与明细。"""
    from apps.kiln.models import BlendTicket, BlendTicketLine

    validate_blend_lines(planned_total_kg=planned_total_kg, lines=lines)
    ticket = BlendTicket.objects.create(
        blendDate=blend_date,
        targetGrade=target_grade,
        plannedTotalKg=planned_total_kg,
        openedBy=opened_by,
    )
    BlendTicketLine.objects.bulk_create(
        [BlendTicketLine(ticket=ticket, resinLot=lot, countedKg=kg) for lot, kg in lines]
    )
    return ticket


@transaction.atomic
def close_blend_ticket(ticket, user):
    """
    结案：仅主管可结案；结案前按开单同一套规则复核明细，
    通过后才写入结案时刻，明细来脂批随之解锁、允许挂灶开值守。
    """
    if ticket.closedAt is not None:
        raise ValidationError("该拼配单已结案，无需重复结案。")
    if not (user.is_staff or user.is_superuser):
        raise PermissionDenied("只有主管才能结案拼配单。")

    lines = [
        (line.resinLot, line.countedKg)
        for line in ticket.lines.select_related("resinLot")
    ]
    validate_blend_lines(
        planned_total_kg=ticket.plannedTotalKg,
        lines=lines,
        exclude_ticket_id=ticket.pk,
    )
    ticket.closedBy = user
    ticket.closedAt = timezone.now()
    ticket.save(update_fields=["closedBy", "closedAt"])
    return ticket
