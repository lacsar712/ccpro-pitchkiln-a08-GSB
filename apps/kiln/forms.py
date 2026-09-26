from django import forms
from django.core.exceptions import ValidationError as DjangoValidationError
from django.forms import inlineformset_factory
from django.forms.models import BaseInlineFormSet
from django.utils import timezone

from .models import (
    BlendTicket,
    BlendTicketLine,
    CookRun,
    FireHearth,
    ResinLot,
    SoftPointProbe,
)
from .services import blend_rules
from .services.floor_rules import assert_can_enter_drawing


class ResinLotForm(forms.ModelForm):
    class Meta:
        model = ResinLot
        fields = ["lotCode", "originPlace", "arrivalKg", "receivedAt"]
        widgets = {
            "lotCode": forms.TextInput(attrs={"class": "field"}),
            "originPlace": forms.TextInput(attrs={"class": "field"}),
            "arrivalKg": forms.NumberInput(attrs={"class": "field", "step": "0.01"}),
            "receivedAt": forms.DateTimeInput(
                attrs={"class": "field", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["receivedAt"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ]
        if self.instance and self.instance.pk and self.instance.receivedAt:
            local = timezone.localtime(self.instance.receivedAt)
            self.initial["receivedAt"] = local.strftime("%Y-%m-%dT%H:%M")


class PhaseChangeForm(forms.Form):
    phase = forms.ChoiceField(
        label="相位",
        choices=FireHearth.PHASE_CHOICES,
        widget=forms.Select(attrs={"class": "field"}),
    )

    def __init__(self, *args, hearth=None, **kwargs):
        self.hearth = hearth
        super().__init__(*args, **kwargs)
        if hearth is not None and not self.is_bound:
            self.fields["phase"].initial = hearth.phase

    def clean_phase(self):
        phase = self.cleaned_data["phase"]
        if self.hearth is not None and phase == FireHearth.PHASE_DRAWING:
            assert_can_enter_drawing(self.hearth)
        return phase


class SoftPointProbeForm(forms.ModelForm):
    class Meta:
        model = SoftPointProbe
        fields = ["sampledAt", "softPointC", "samplerName"]
        widgets = {
            "sampledAt": forms.DateTimeInput(
                attrs={"class": "field", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "softPointC": forms.NumberInput(attrs={"class": "field", "step": "0.01"}),
            "samplerName": forms.TextInput(attrs={"class": "field"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sampledAt"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ]
        if not self.is_bound and not (self.instance and self.instance.pk):
            self.initial["sampledAt"] = timezone.localtime().strftime("%Y-%m-%dT%H:%M")


class ResinLotChoiceField(forms.ModelChoiceField):
    """来脂批下拉：被未结案拼配单锁定的批在选项里直接标注。"""

    locked_ids = frozenset()

    def label_from_instance(self, obj):
        label = f"{obj.lotCode} · {obj.originPlace} · {obj.arrivalKg}kg"
        if obj.pk in self.locked_ids:
            label += "（拼配锁定）"
        return label


class OpenCookRunForm(forms.ModelForm):
    resinLot = ResinLotChoiceField(
        label="来脂批",
        queryset=ResinLot.objects.all(),
        widget=forms.Select(attrs={"class": "field"}),
    )

    class Meta:
        model = CookRun
        fields = ["resinLot", "openedAt", "targetSoftPointC"]
        widgets = {
            "openedAt": forms.DateTimeInput(
                attrs={"class": "field", "type": "datetime-local"},
                format="%Y-%m-%dT%H:%M",
            ),
            "targetSoftPointC": forms.NumberInput(
                attrs={"class": "field", "step": "0.01"}
            ),
        }

    def __init__(self, *args, hearth=None, **kwargs):
        self.hearth = hearth
        super().__init__(*args, **kwargs)
        self.fields["openedAt"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
        ]
        self.fields["resinLot"].locked_ids = blend_rules.locked_lot_ids()
        if not self.is_bound:
            self.initial["openedAt"] = timezone.localtime().strftime("%Y-%m-%dT%H:%M")

    def clean_resinLot(self):
        lot = self.cleaned_data["resinLot"]
        # 开灶挂批核验：与结案核验同源于 blend_rules
        blend_rules.assert_lot_free_for_hearth(lot)
        return lot

    def clean(self):
        cleaned = super().clean()
        if self.hearth is not None and self.hearth.open_run() is not None:
            raise forms.ValidationError("该灶已有进行中的值守，请先收灶再开新灶。")
        return cleaned


class BlendTicketForm(forms.ModelForm):
    """拼配单单头：拼配日、目标品级、计划总重；结案时刻初始留空。"""

    class Meta:
        model = BlendTicket
        fields = ["blendDate", "targetGrade", "plannedTotalKg"]
        widgets = {
            "blendDate": forms.DateInput(
                attrs={"class": "field", "type": "date"},
                format="%Y-%m-%d",
            ),
            "targetGrade": forms.TextInput(attrs={"class": "field"}),
            "plannedTotalKg": forms.NumberInput(
                attrs={"class": "field", "step": "0.01"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["blendDate"].input_formats = ["%Y-%m-%d"]
        if not self.is_bound:
            self.initial["blendDate"] = timezone.localdate().strftime("%Y-%m-%d")

    def clean_plannedTotalKg(self):
        total = self.cleaned_data["plannedTotalKg"]
        if total is not None and total <= 0:
            raise forms.ValidationError("计划总重必须大于 0。")
        return total


class BlendTicketLineForm(forms.ModelForm):
    resinLot = ResinLotChoiceField(
        label="来脂批",
        queryset=ResinLot.objects.all(),
        widget=forms.Select(attrs={"class": "field"}),
    )

    class Meta:
        model = BlendTicketLine
        fields = ["resinLot", "countedKg"]
        widgets = {
            "countedKg": forms.NumberInput(attrs={"class": "field", "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["resinLot"].locked_ids = blend_rules.locked_lot_ids()


class BaseBlendLineFormSet(BaseInlineFormSet):
    """明细整体核验：与结案核验同源地调用 blend_rules.validate_blend_lines。"""

    def __init__(self, *args, planned_total=None, **kwargs):
        self.planned_total = planned_total
        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        lines = []
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            lot = form.cleaned_data.get("resinLot")
            kg = form.cleaned_data.get("countedKg")
            if lot is None or kg is None:
                continue
            lines.append((lot, kg))
        exclude = self.instance if self.instance and self.instance.pk else None
        try:
            blend_rules.validate_blend_lines(
                planned_total=self.planned_total,
                lines=lines,
                exclude_ticket=exclude,
            )
        except DjangoValidationError as exc:
            raise forms.ValidationError(exc.messages)


BlendLineFormSet = inlineformset_factory(
    BlendTicket,
    BlendTicketLine,
    form=BlendTicketLineForm,
    formset=BaseBlendLineFormSet,
    extra=3,
    can_delete=False,
)
