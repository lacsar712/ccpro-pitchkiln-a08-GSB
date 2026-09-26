from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    BlendLineFormSet,
    BlendTicketForm,
    OpenCookRunForm,
    PhaseChangeForm,
    ResinLotForm,
    SoftPointProbeForm,
)
from .models import BlendTicket, CookRun, FireHearth, ResinLot
from .services.blend_rules import (
    close_blend_ticket,
    lot_blend_status_map,
    open_blend_ticket,
)
from .services.floor_rules import change_hearth_phase


def _wants_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _hearths_for_board():
    return FireHearth.objects.prefetch_related(
        Prefetch(
            "runs",
            queryset=CookRun.objects.filter(closedAt__isnull=True)
            .select_related("resinLot")
            .prefetch_related("probes"),
            to_attr="open_runs_cache",
        )
    ).order_by("lane", "tag")


def _board_context():
    hearths = list(_hearths_for_board())
    lanes = {}
    for h in hearths:
        lanes.setdefault(h.lane, []).append(h)
    phase_legend = [
        (key, label, sum(1 for h in hearths if h.phase == key))
        for key, label in FireHearth.PHASE_CHOICES
    ]
    return {
        "hearths": hearths,
        "lanes": sorted(lanes.items()),
        "phase_legend": phase_legend,
    }


def _drawer_context(hearth):
    open_run = hearth.open_run()
    probes = []
    if open_run:
        probes = list(open_run.probes.order_by("-sampledAt", "-id"))
    return {
        "hearth": hearth,
        "open_run": open_run,
        "probes": probes,
        "phase_form": PhaseChangeForm(hearth=hearth),
        "probe_form": SoftPointProbeForm() if open_run else None,
        "open_run_form": OpenCookRunForm(hearth=hearth) if open_run is None else None,
    }


@login_required
def home(request):
    ctx = _board_context()
    drawer_pk = request.GET.get("hearth")
    if drawer_pk:
        try:
            hearth = FireHearth.objects.get(pk=drawer_pk)
            ctx.update(_drawer_context(hearth))
            ctx["drawer_open"] = True
        except (FireHearth.DoesNotExist, ValueError):
            ctx["drawer_open"] = False
    else:
        ctx["drawer_open"] = False
    return render(request, "floor/board.html", ctx)


@login_required
def floor_grid_partial(request):
    html = render_to_string("floor/_grid.html", _board_context(), request=request)
    return HttpResponse(html)


@login_required
def hearth_drawer(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    ctx = _drawer_context(hearth)
    if _wants_htmx(request):
        return render(request, "floor/_drawer.html", ctx)
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def change_phase(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    form = PhaseChangeForm(request.POST, hearth=hearth)
    if form.is_valid():
        try:
            change_hearth_phase(hearth, form.cleaned_data["phase"])
            messages.success(request, f"灶牌 {hearth.tag} 相位已更新")
        except ValidationError as exc:
            msg = (
                exc.message_dict.get("phase") if hasattr(exc, "message_dict") else None
            )
            messages.error(request, msg[0] if msg else str(exc))
    else:
        err = form.errors.get("phase")
        messages.error(request, err[0] if err else "相位切换失败")

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def add_probe(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    open_run = hearth.open_run()
    if open_run is None:
        messages.error(request, "没有进行中的值守，无法登记探针")
        return redirect(f"/?hearth={pk}")

    form = SoftPointProbeForm(request.POST)
    if form.is_valid():
        probe = form.save(commit=False)
        probe.run = open_run
        probe.save()
        messages.success(request, f"已登记探针 {probe.softPointC}℃")
    else:
        messages.error(request, "探针登记失败，请检查输入")

    if _wants_htmx(request):
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def open_run(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    form = OpenCookRunForm(request.POST, hearth=hearth)
    if form.is_valid():
        run = form.save(commit=False)
        run.hearth = hearth
        run.save()
        if hearth.phase == FireHearth.PHASE_COLD:
            hearth.phase = FireHearth.PHASE_CHARGING
            hearth.save(update_fields=["phase"])
        messages.success(request, "新值守已开灶")
    else:
        for errs in form.errors.values():
            for e in errs:
                messages.error(request, e)
            break

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_POST
def close_run(request, pk):
    hearth = get_object_or_404(FireHearth, pk=pk)
    open_run = hearth.open_run()
    if open_run is None:
        messages.error(request, "没有进行中的值守可收灶")
    else:
        open_run.closedAt = timezone.now()
        open_run.save(update_fields=["closedAt"])
        hearth.phase = FireHearth.PHASE_COLD
        hearth.save(update_fields=["phase"])
        messages.success(request, "值守已收灶，灶台回冷灶")

    if _wants_htmx(request):
        hearth.refresh_from_db()
        resp = render(request, "floor/_drawer.html", _drawer_context(hearth))
        resp["HX-Trigger"] = "floor-refresh"
        return resp
    return redirect(f"/?hearth={pk}")


@login_required
@require_http_methods(["GET", "POST"])
def resin_lot_feed(request):
    if request.method == "POST":
        form = ResinLotForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "来脂批已登记")
            return redirect("resin_lot_feed")
    else:
        form = ResinLotForm(
            initial={
                "receivedAt": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
            }
        )

    lots = list(ResinLot.objects.all()[:40])
    blend_status = lot_blend_status_map(lots)
    for lot in lots:
        state = blend_status.get(lot.pk)
        lot.blend_state = state[0] if state else None
        lot.blend_ticket = state[1] if state else None
    return render(request, "resin/feed.html", {"lots": lots, "form": form})


@login_required
@require_http_methods(["GET", "POST"])
def blend_ticket_board(request):
    """拼配单看板：开单（单头 + 明细行）与未结案/已结案列表。"""
    if request.method == "POST":
        form = BlendTicketForm(request.POST)
        formset = BlendLineFormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            lines = [
                (f.cleaned_data["resinLot"], f.cleaned_data["countedKg"])
                for f in formset.forms
                if f.cleaned_data.get("resinLot")
            ]
            try:
                ticket = open_blend_ticket(
                    blend_date=form.cleaned_data["blendDate"],
                    target_grade=form.cleaned_data["targetGrade"],
                    planned_total_kg=form.cleaned_data["plannedTotalKg"],
                    opened_by=request.user,
                    lines=lines,
                )
                messages.success(
                    request,
                    f"拼配单 #{ticket.pk} 已开单，明细来脂批已锁定，待主管结案",
                )
                return redirect("blend_ticket_board")
            except ValidationError as exc:
                for msg in exc.messages:
                    messages.error(request, msg)
        else:
            messages.error(request, "开单失败，请检查单头与明细行输入")
    else:
        form = BlendTicketForm()
        formset = BlendLineFormSet()

    tickets = BlendTicket.objects.select_related("openedBy", "closedBy").prefetch_related(
        "lines__resinLot"
    )[:40]
    return render(
        request,
        "blend/board.html",
        {"tickets": tickets, "form": form, "formset": formset},
    )


@login_required
@require_POST
def blend_ticket_close(request, pk):
    """主管结案：复核同源核验后写结案时刻，明细来脂批解锁。"""
    ticket = get_object_or_404(BlendTicket, pk=pk)
    try:
        close_blend_ticket(ticket, request.user)
        messages.success(
            request, f"拼配单 #{ticket.pk} 已结案，明细来脂批解除锁定、可挂灶开值守"
        )
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    except ValidationError as exc:
        for msg in exc.messages:
            messages.error(request, msg)
    return redirect("blend_ticket_board")
