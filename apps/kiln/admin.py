from django.contrib import admin

from .models import (
    BlendTicket,
    BlendTicketLine,
    CookRun,
    FireHearth,
    ResinLot,
    SoftPointProbe,
)


@admin.register(ResinLot)
class ResinLotAdmin(admin.ModelAdmin):
    list_display = ("id", "lotCode", "originPlace", "arrivalKg", "receivedAt")
    search_fields = ("lotCode", "originPlace")


@admin.register(FireHearth)
class FireHearthAdmin(admin.ModelAdmin):
    list_display = ("id", "lane", "tag", "resinGrade", "phase")
    list_filter = ("phase", "lane")
    search_fields = ("tag", "resinGrade")


@admin.register(CookRun)
class CookRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "hearth",
        "resinLot",
        "openedAt",
        "closedAt",
        "targetSoftPointC",
    )
    list_filter = ("hearth",)
    search_fields = ("hearth__tag", "resinLot__lotCode")


@admin.register(SoftPointProbe)
class SoftPointProbeAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "sampledAt", "softPointC", "samplerName")
    search_fields = ("samplerName",)


class BlendTicketLineInline(admin.TabularInline):
    model = BlendTicketLine
    extra = 0


@admin.register(BlendTicket)
class BlendTicketAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "blendDate",
        "targetGrade",
        "plannedTotalKg",
        "createdBy",
        "closedAt",
    )
    list_filter = ("closedAt",)
    search_fields = ("targetGrade", "lines__resinLot__lotCode")
    inlines = [BlendTicketLineInline]
