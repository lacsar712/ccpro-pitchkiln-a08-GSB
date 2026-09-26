from django import forms
from django.utils import timezone

from .models import (
    BlendTicket,
    BlendTicketLine,
    CookRun,
    FireHearth,
    ResinLot,
    SoftPointProbe,
)
from .services.blend_rules import assert_lot_ready_for_run, locked_lot_ids
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


class OpenCookRunForm(forms.ModelForm):
    class Meta:
        model = CookRun
        fields = ["resinLot", "openedAt", "targetSoftPointC"]
        widgets = {
            "resinLot": forms.Select(attrs={"class": "field"}),
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
        lot_field = self.fields["resinLot"]
        lot_field.queryset = ResinLot.objects.all()
        locked = locked_lot_ids()
        lot_field.label_from_instance = lambda lot: (
            f"{lot.lotCode} · {lot.originPlace}"
            + ("（拼配锁定·未结案）" if lot.pk in locked else "")
        )
        if not self.is_bound:
            self.initial["openedAt"] = timezone.localtime().strftime("%Y-%m-%dT%H:%M")

    def clean_resinLot(self):
        lot = self.cleaned_data["resinLot"]
        assert_lot_ready_for_run(lot)
        return lot

    def clean(self):
        cleaned = super().clean()
        if self.hearth is not None and self.hearth.open_run() is not None:
            raise forms.ValidationError("该灶已有进行中的值守，请先收灶再开新灶。")
        return cleaned


class BlendTicketForm(forms.ModelForm):
    """拼配单单头：拼配日、目标品级、计划总重（开单人由视图写入）。"""

    class Meta:
        model = BlendTicket
        fields = ["blendDate", "targetGrade", "plannedTotalKg"]
        widgets = {
            "blendDate": forms.DateInput(
                attrs={"class": "field", "type": "date"}, format="%Y-%m-%d"
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


class BlendTicketLineForm(forms.ModelForm):
    """拼配明细行：来脂批 + 计入千克；下拉标注到货量与锁定状态。"""

    class Meta:
        model = BlendTicketLine
        fields = ["resinLot", "countedKg"]
        widgets = {
            "resinLot": forms.Select(attrs={"class": "field"}),
            "countedKg": forms.NumberInput(attrs={"class": "field", "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        lot_field = self.fields["resinLot"]
        lot_field.queryset = ResinLot.objects.all()
        locked = locked_lot_ids()
        lot_field.label_from_instance = lambda lot: (
            f"{lot.lotCode} · 到货 {lot.arrivalKg} kg"
            + ("（拼配锁定·未结案）" if lot.pk in locked else "")
        )


BlendLineFormSet = forms.inlineformset_factory(
    BlendTicket,
    BlendTicketLine,
    form=BlendTicketLineForm,
    extra=2,
    can_delete=False,
)

