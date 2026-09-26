# Generated manually for PitchKiln-01 blend ticket domain

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kiln", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BlendTicket",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("blendDate", models.DateField(verbose_name="拼配日")),
                (
                    "targetGrade",
                    models.CharField(max_length=80, verbose_name="目标品级"),
                ),
                (
                    "plannedTotalKg",
                    models.DecimalField(
                        decimal_places=2, max_digits=10, verbose_name="计划总重(kg)"
                    ),
                ),
                (
                    "openedAt",
                    models.DateTimeField(auto_now_add=True, verbose_name="开单时刻"),
                ),
                (
                    "closedAt",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="结案时刻"
                    ),
                ),
                (
                    "closedBy",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="closed_blend_tickets",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="结案人",
                    ),
                ),
                (
                    "openedBy",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="opened_blend_tickets",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="开单人",
                    ),
                ),
            ],
            options={
                "verbose_name": "脂液拼配单",
                "verbose_name_plural": "脂液拼配单",
                "ordering": ["-openedAt", "-id"],
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(plannedTotalKg__gt=0),
                        name="blendticket_planned_total_positive",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="BlendTicketLine",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "countedKg",
                    models.DecimalField(
                        decimal_places=2, max_digits=10, verbose_name="计入千克"
                    ),
                ),
                (
                    "resinLot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="blend_lines",
                        to="kiln.resinlot",
                        verbose_name="来脂批",
                    ),
                ),
                (
                    "ticket",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="kiln.blendticket",
                        verbose_name="拼配单",
                    ),
                ),
            ],
            options={
                "verbose_name": "拼配明细",
                "verbose_name_plural": "拼配明细",
                "ordering": ["id"],
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(countedKg__gt=0),
                        name="blendticketline_counted_positive",
                    ),
                    models.UniqueConstraint(
                        fields=("ticket", "resinLot"),
                        name="blendticketline_unique_lot_per_ticket",
                    ),
                ],
            },
        ),
    ]
