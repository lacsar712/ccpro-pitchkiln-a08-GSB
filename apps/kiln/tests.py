from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import OpenCookRunForm
from .models import BlendTicket, BlendTicketLine, CookRun, FireHearth, ResinLot
from .services import blend_rules


def make_lot(code, kg):
    return ResinLot.objects.create(
        lotCode=code,
        originPlace="松脂坳",
        arrivalKg=Decimal(kg),
        receivedAt=timezone.now(),
    )


class BlendRulesTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("boss", "b@x.local", "pw123456")
        self.worker = User.objects.create_user("w1", "w@x.local", "pw123456")
        self.lot1 = make_lot("脂-松脂坳-T1", "1000.00")
        self.lot2 = make_lot("脂-松脂坳-T2", "500.00")
        self.lot3 = make_lot("脂-桐油坑-T3", "800.00")
        self.hearth = FireHearth.objects.create(
            lane=1, tag="坳火-测", resinGrade="特级脂"
        )

    def make_ticket(self, lines, planned, user=None):
        ticket = BlendTicket.objects.create(
            blendDate=timezone.localdate(),
            targetGrade="特级脂",
            plannedTotalKg=Decimal(planned),
            createdBy=user or self.admin,
        )
        for lot, kg in lines:
            BlendTicketLine.objects.create(
                ticket=ticket, resinLot=lot, countedKg=Decimal(kg)
            )
        return ticket

    def open_run_form(self, lot):
        return OpenCookRunForm(
            data={
                "resinLot": lot.pk,
                "openedAt": "2026-09-26T08:00",
                "targetSoftPointC": "88.00",
            },
            hearth=self.hearth,
        )

    # —— 开单核验 ——

    def test_sum_must_exactly_equal_planned_total(self):
        with self.assertRaises(ValidationError) as ctx:
            blend_rules.validate_blend_lines(
                planned_total=Decimal("1500"),
                lines=[(self.lot1, Decimal("1000")), (self.lot2, Decimal("400"))],
            )
        self.assertIn("精确等于计划总重", str(ctx.exception.messages))

    def test_exact_sum_passes(self):
        blend_rules.validate_blend_lines(
            planned_total=Decimal("1500.00"),
            lines=[(self.lot1, Decimal("1000.00")), (self.lot2, Decimal("500"))],
        )

    def test_line_cannot_exceed_arrival_kg(self):
        with self.assertRaises(ValidationError) as ctx:
            blend_rules.validate_blend_lines(
                planned_total=Decimal("1200"),
                lines=[(self.lot1, Decimal("1200"))],
            )
        self.assertIn("大于该批到货", str(ctx.exception.messages))

    def test_duplicate_lot_in_one_ticket_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            blend_rules.validate_blend_lines(
                planned_total=Decimal("1500"),
                lines=[(self.lot1, Decimal("1000")), (self.lot1, Decimal("500"))],
            )
        self.assertIn("重复", str(ctx.exception.messages))

    def test_lot_cannot_enter_second_unsettled_ticket(self):
        self.make_ticket([(self.lot1, "1000")], "1000")
        with self.assertRaises(ValidationError) as ctx:
            blend_rules.validate_blend_lines(
                planned_total=Decimal("1000"),
                lines=[(self.lot1, Decimal("1000"))],
            )
        self.assertIn("不得再进另一张未结案单", str(ctx.exception.messages))

    # —— 未结案锁定：禁止开新值守 ——

    def test_locked_lot_cannot_open_run(self):
        self.make_ticket([(self.lot1, "1000")], "1000")
        form = self.open_run_form(self.lot1)
        self.assertFalse(form.is_valid())
        self.assertIn("结案", str(form.errors["resinLot"]))
        self.assertEqual(CookRun.objects.count(), 0)

    def test_free_lot_can_open_run(self):
        form = self.open_run_form(self.lot3)
        self.assertTrue(form.is_valid(), form.errors)

    # —— 结案 ——

    def test_close_unlocks_lots_for_hearth(self):
        ticket = self.make_ticket([(self.lot1, "1000")], "1000")
        self.assertIsNotNone(blend_rules.lot_lock_ticket(self.lot1))
        blend_rules.close_ticket(ticket, self.admin)
        self.assertIsNone(blend_rules.lot_lock_ticket(self.lot1))
        self.assertTrue(self.open_run_form(self.lot1).is_valid())

    def test_close_requires_supervisor(self):
        ticket = self.make_ticket([(self.lot1, "1000")], "1000")
        with self.assertRaises(ValidationError) as ctx:
            blend_rules.close_ticket(ticket, self.worker)
        self.assertIn("主管", str(ctx.exception.messages))
        ticket.refresh_from_db()
        self.assertIsNone(ticket.closedAt)

    def test_close_revalidates_lines_with_same_rules(self):
        ticket = self.make_ticket([(self.lot1, "1000")], "1000")
        # 开单后明细被改坏（如后台直接改库），结案核验必须拦下
        line = ticket.lines.get()
        line.countedKg = Decimal("900")
        line.save()
        with self.assertRaises(ValidationError) as ctx:
            blend_rules.close_ticket(ticket, self.admin)
        self.assertIn("精确等于计划总重", str(ctx.exception.messages))
        ticket.refresh_from_db()
        self.assertIsNone(ticket.closedAt)

    def test_closed_ticket_lot_can_enter_new_ticket(self):
        ticket = self.make_ticket([(self.lot1, "1000")], "1000")
        blend_rules.close_ticket(ticket, self.admin)
        blend_rules.validate_blend_lines(
            planned_total=Decimal("1000"),
            lines=[(self.lot1, Decimal("1000"))],
        )


class BlendViewTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser("boss", "b@x.local", "pw123456")
        self.worker = User.objects.create_user("w1", "w@x.local", "pw123456")
        self.lot1 = make_lot("脂-松脂坳-V1", "1000.00")
        self.lot2 = make_lot("脂-桐油坑-V2", "500.00")
        self.hearth = FireHearth.objects.create(
            lane=1, tag="坳火-视", resinGrade="特级脂"
        )

    def post_data(self, planned, lines):
        data = {
            "blendDate": "2026-09-26",
            "targetGrade": "特级脂",
            "plannedTotalKg": planned,
            "lines-TOTAL_FORMS": str(len(lines)),
            "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0",
            "lines-MAX_NUM_FORMS": "1000",
        }
        for i, (lot, kg) in enumerate(lines):
            data[f"lines-{i}-resinLot"] = str(lot.pk)
            data[f"lines-{i}-countedKg"] = kg
        return data

    def test_open_ticket_rejects_bad_sum(self):
        self.client.login(username="boss", password="pw123456")
        resp = self.client.post(
            reverse("blend_board"),
            self.post_data("1500", [(self.lot1, "1000"), (self.lot2, "400")]),
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "精确等于计划总重")
        self.assertEqual(BlendTicket.objects.count(), 0)

    def test_open_ticket_creates_and_locks_lots(self):
        self.client.login(username="boss", password="pw123456")
        resp = self.client.post(
            reverse("blend_board"),
            self.post_data("1500", [(self.lot1, "1000"), (self.lot2, "500")]),
        )
        self.assertRedirects(resp, reverse("blend_board"))
        ticket = BlendTicket.objects.get()
        self.assertIsNone(ticket.closedAt)
        self.assertEqual(ticket.createdBy, self.admin)
        self.assertEqual(blend_rules.locked_lot_ids(), {self.lot1.pk, self.lot2.pk})

        # 未结案期间：开灶视图同样拦截锁定批
        resp = self.client.post(
            reverse("open_run", args=[self.hearth.pk]),
            {
                "resinLot": self.lot1.pk,
                "openedAt": "2026-09-26T08:00",
                "targetSoftPointC": "88.00",
            },
        )
        self.assertEqual(CookRun.objects.count(), 0)

        # 来脂批卡片标注拼配锁定
        resp = self.client.get(reverse("resin_lot_feed"))
        self.assertContains(resp, "拼配锁定")

    def test_close_view_requires_superuser(self):
        self.client.login(username="boss", password="pw123456")
        self.client.post(
            reverse("blend_board"),
            self.post_data("1000", [(self.lot1, "1000")]),
        )
        ticket = BlendTicket.objects.get()

        self.client.login(username="w1", password="pw123456")
        self.client.post(reverse("blend_close", args=[ticket.pk]))
        ticket.refresh_from_db()
        self.assertIsNone(ticket.closedAt)

        self.client.login(username="boss", password="pw123456")
        self.client.post(reverse("blend_close", args=[ticket.pk]))
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.closedAt)
        self.assertEqual(blend_rules.locked_lot_ids(), set())
