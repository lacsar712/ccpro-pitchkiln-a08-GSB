from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import BlendLineFormSet, OpenCookRunForm
from .models import BlendTicket, CookRun, FireHearth, ResinLot
from .services.blend_rules import (
    assert_lot_ready_for_run,
    close_blend_ticket,
    locked_lot_ids,
    lot_blend_status_map,
    open_blend_ticket,
)

FORMSET_PREFIX = BlendLineFormSet.get_default_prefix()


class BlendRuleTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.boss = User.objects.create_superuser("boss", "boss@x.local", "pw123456")
        cls.worker = User.objects.create_user("hand", "hand@x.local", "pw123456")
        cls.lot_a = ResinLot.objects.create(
            lotCode="脂-试-A",
            originPlace="试场甲",
            arrivalKg=Decimal("1000.00"),
            receivedAt=timezone.now(),
        )
        cls.lot_b = ResinLot.objects.create(
            lotCode="脂-试-B",
            originPlace="试场乙",
            arrivalKg=Decimal("500.00"),
            receivedAt=timezone.now(),
        )
        cls.lot_c = ResinLot.objects.create(
            lotCode="脂-试-C",
            originPlace="试场丙",
            arrivalKg=Decimal("300.00"),
            receivedAt=timezone.now(),
        )
        cls.hearth = FireHearth.objects.create(
            lane=1, tag="试灶-甲", resinGrade="特级脂"
        )

    def open_ticket(self, lots_kgs, planned):
        return open_blend_ticket(
            blend_date=timezone.localdate(),
            target_grade="特级脂",
            planned_total_kg=Decimal(planned),
            opened_by=self.worker,
            lines=[(lot, Decimal(kg)) for lot, kg in lots_kgs],
        )

    def blend_post_data(self, planned, *rows):
        data = {
            "blendDate": "2026-09-26",
            "targetGrade": "特级脂",
            "plannedTotalKg": planned,
            f"{FORMSET_PREFIX}-TOTAL_FORMS": str(len(rows)),
            f"{FORMSET_PREFIX}-INITIAL_FORMS": "0",
            f"{FORMSET_PREFIX}-MIN_NUM_FORMS": "0",
            f"{FORMSET_PREFIX}-MAX_NUM_FORMS": "1000",
        }
        for i, (lot, kg) in enumerate(rows):
            data[f"{FORMSET_PREFIX}-{i}-resinLot"] = str(lot.pk)
            data[f"{FORMSET_PREFIX}-{i}-countedKg"] = kg
        return data


class OpenTicketTests(BlendRuleTestCase):
    def test_happy_path_locks_lots(self):
        ticket = self.open_ticket(
            [(self.lot_a, "700.00"), (self.lot_b, "300.00")], "1000.00"
        )
        self.assertIsNone(ticket.closedAt)
        self.assertEqual(ticket.lines.count(), 2)
        self.assertEqual(locked_lot_ids(), {self.lot_a.pk, self.lot_b.pk})
        self.assertNotIn(self.lot_c.pk, locked_lot_ids())

    def test_sum_must_equal_planned_exactly(self):
        with self.assertRaises(ValidationError):
            self.open_ticket([(self.lot_a, "700.00")], "700.01")
        self.assertEqual(BlendTicket.objects.count(), 0)

    def test_sum_shortfall_rejected(self):
        with self.assertRaises(ValidationError):
            self.open_ticket(
                [(self.lot_a, "700.00"), (self.lot_b, "200.00")], "1000.00"
            )
        self.assertEqual(BlendTicket.objects.count(), 0)

    def test_line_cannot_exceed_arrival_kg(self):
        with self.assertRaises(ValidationError):
            self.open_ticket([(self.lot_c, "300.01")], "300.01")

    def test_same_lot_cannot_repeat_in_one_ticket(self):
        with self.assertRaises(ValidationError):
            self.open_ticket(
                [(self.lot_a, "600.00"), (self.lot_a, "400.00")], "1000.00"
            )

    def test_lot_cannot_enter_second_unclosed_ticket(self):
        self.open_ticket([(self.lot_a, "1000.00")], "1000.00")
        with self.assertRaises(ValidationError):
            self.open_ticket(
                [(self.lot_a, "500.00"), (self.lot_b, "500.00")], "1000.00"
            )
        self.assertEqual(BlendTicket.objects.count(), 1)


class RunAttachLockTests(BlendRuleTestCase):
    """未结案拼配单的来脂批禁止挂灶开新值守；结案后放行。"""

    def setUp(self):
        self.ticket = self.open_ticket(
            [(self.lot_a, "600.00"), (self.lot_b, "400.00")], "1000.00"
        )

    def test_locked_lot_rejected_by_rule(self):
        with self.assertRaises(ValidationError):
            assert_lot_ready_for_run(self.lot_a)
        # 不在未结案单里的批不受影响
        assert_lot_ready_for_run(self.lot_c)

    def test_locked_lot_rejected_by_open_run_form(self):
        form = OpenCookRunForm(
            data={
                "resinLot": str(self.lot_a.pk),
                "openedAt": "2026-09-26T08:00",
                "targetSoftPointC": "88.00",
            },
            hearth=self.hearth,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("resinLot", form.errors)

    def test_locked_lot_rejected_by_open_run_view(self):
        self.client.force_login(self.worker)
        resp = self.client.post(
            reverse("open_run", args=[self.hearth.pk]),
            {
                "resinLot": str(self.lot_a.pk),
                "openedAt": "2026-09-26T08:00",
                "targetSoftPointC": "88.00",
            },
        )
        self.assertEqual(CookRun.objects.count(), 0)
        self.assertRedirects(resp, f"/?hearth={self.hearth.pk}")

    def test_close_releases_lots_for_run(self):
        close_blend_ticket(self.ticket, self.boss)
        self.assertEqual(locked_lot_ids(), set())
        assert_lot_ready_for_run(self.lot_a)
        run = CookRun.objects.create(
            hearth=self.hearth,
            resinLot=self.lot_a,
            openedAt=timezone.now(),
            targetSoftPointC=Decimal("88.00"),
        )
        self.assertIsNone(run.closedAt)

    def test_closed_ticket_lot_can_enter_new_ticket(self):
        close_blend_ticket(self.ticket, self.boss)
        ticket2 = self.open_ticket([(self.lot_a, "600.00")], "600.00")
        self.assertIsNone(ticket2.closedAt)


class CloseTicketTests(BlendRuleTestCase):
    def setUp(self):
        self.ticket = self.open_ticket(
            [(self.lot_a, "600.00"), (self.lot_b, "400.00")], "1000.00"
        )

    def test_only_staff_can_close(self):
        with self.assertRaises(PermissionDenied):
            close_blend_ticket(self.ticket, self.worker)
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.closedAt)

    def test_close_stamps_time_and_closer(self):
        close_blend_ticket(self.ticket, self.boss)
        self.ticket.refresh_from_db()
        self.assertIsNotNone(self.ticket.closedAt)
        self.assertEqual(self.ticket.closedBy, self.boss)

    def test_double_close_rejected(self):
        close_blend_ticket(self.ticket, self.boss)
        with self.assertRaises(ValidationError):
            close_blend_ticket(self.ticket, self.boss)

    def test_close_rechecks_lines_with_same_rules(self):
        # 开单后绕过服务直接改明细：结案复核必须与开单同源拦下
        line = self.ticket.lines.get(resinLot=self.lot_b)
        line.countedKg = Decimal("399.00")
        line.save(update_fields=["countedKg"])
        with self.assertRaises(ValidationError):
            close_blend_ticket(self.ticket, self.boss)
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.closedAt)

    def test_close_view_forbidden_for_worker(self):
        self.client.force_login(self.worker)
        self.client.post(reverse("blend_ticket_close", args=[self.ticket.pk]))
        self.ticket.refresh_from_db()
        self.assertIsNone(self.ticket.closedAt)

    def test_close_view_ok_for_staff(self):
        self.client.force_login(self.boss)
        resp = self.client.post(reverse("blend_ticket_close", args=[self.ticket.pk]))
        self.assertRedirects(resp, reverse("blend_ticket_board"))
        self.ticket.refresh_from_db()
        self.assertIsNotNone(self.ticket.closedAt)


class BlendBoardViewTests(BlendRuleTestCase):
    def test_open_ticket_via_view(self):
        self.client.force_login(self.worker)
        resp = self.client.post(
            reverse("blend_ticket_board"),
            self.blend_post_data(
                "1000.00", (self.lot_a, "600.00"), (self.lot_b, "400.00")
            ),
        )
        self.assertRedirects(resp, reverse("blend_ticket_board"))
        ticket = BlendTicket.objects.get()
        self.assertEqual(ticket.openedBy, self.worker)
        self.assertEqual(ticket.counted_total_kg(), Decimal("1000.00"))

    def test_view_rejects_bad_sum(self):
        self.client.force_login(self.worker)
        self.client.post(
            reverse("blend_ticket_board"),
            self.blend_post_data(
                "999.00", (self.lot_a, "600.00"), (self.lot_b, "400.00")
            ),
        )
        self.assertEqual(BlendTicket.objects.count(), 0)


class LotBlendStatusTests(BlendRuleTestCase):
    def test_status_map_locked_then_released(self):
        self.assertEqual(lot_blend_status_map([self.lot_a]), {})
        ticket = self.open_ticket([(self.lot_a, "1000.00")], "1000.00")
        status = lot_blend_status_map([self.lot_a, self.lot_b])
        self.assertEqual(status[self.lot_a.pk][0], "locked")
        self.assertNotIn(self.lot_b.pk, status)
        close_blend_ticket(ticket, self.boss)
        status = lot_blend_status_map([self.lot_a])
        self.assertEqual(status[self.lot_a.pk][0], "released")

    def test_feed_card_marks_locked_lot(self):
        self.open_ticket([(self.lot_a, "1000.00")], "1000.00")
        self.client.force_login(self.worker)
        resp = self.client.get(reverse("resin_lot_feed"))
        self.assertContains(resp, "拼配锁定")
