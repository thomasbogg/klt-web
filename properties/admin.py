from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import parse_qsl

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

NARROW_FIELD_WIDTHS = {
    'start_date': '7em',
    'end_date': '7em',
    'rate': '5.5em',
    'weekly_discount_percent': '4.5em',
    'monthly_discount_percent': '4.5em',
    'last_minute_discount_percent': '4.5em',
    'extra_adult_rate': '5.5em',
    'extra_child_rate': '5.5em',
}

# Register your models here.
from .models import (
    Property, Price, Location, Owner, Accountant, ManagementCompany, PropertyOwnership,
    Amenity, PropertySpec, SEFDetail, iCalLink, PropertyImage,
    LocationImage, LocationSpec, LocationRules,
)


class SpecInline(admin.StackedInline):
    model = PropertySpec
    max_num = 1


class AmenityInline(admin.StackedInline):
    model = Amenity
    max_num = 1


class SEFDetailInline(admin.StackedInline):
    model = SEFDetail
    max_num = 1


class iCalLinkInline(admin.TabularInline):
    model = iCalLink
    extra = 1


class PropertyImageInline(admin.TabularInline):
    model = PropertyImage
    extra = 1


class LocationSpecInline(admin.StackedInline):
    model = LocationSpec
    max_num = 1


class LocationImageInline(admin.TabularInline):
    model = LocationImage
    extra = 1


class LocationRulesInline(admin.StackedInline):
    model = LocationRules
    max_num = 1


class PropertyOwnershipInline(admin.TabularInline):
    model = PropertyOwnership
    extra = 1


class PropertyAdmin(admin.ModelAdmin):
    inlines = [
        SpecInline, AmenityInline, SEFDetailInline, iCalLinkInline, PropertyImageInline,
        PropertyOwnershipInline,
    ]


class PriceBulkToolForm(forms.Form):
    ADJUST = 'adjust'
    CLONE = 'clone'
    MODE_CHOICES = [
        (ADJUST, "Adjust an existing year in place"),
        (CLONE, "Clone a year forward with an adjustment"),
    ]

    mode = forms.ChoiceField(choices=MODE_CHOICES, widget=forms.RadioSelect, initial=ADJUST)
    year = forms.IntegerField(
        required=False, label='Year',
        help_text="Which year's price rows to adjust (adjust mode).",
    )
    source_year = forms.IntegerField(required=False, label='Source year')
    target_year = forms.IntegerField(required=False, label='Target year')
    percent = forms.DecimalField(
        max_digits=5, decimal_places=2,
        help_text="e.g. 5 for +5%, -10 for a 10% cut. Applied to the nightly rate only - "
                   "discount percentages and extra guest rates carry over unchanged.",
    )
    properties = forms.ModelMultipleChoiceField(
        queryset=Property.objects.order_by('title'), required=False,
        help_text="Leave blank to apply to every property with price rows in the relevant year.",
    )

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get('mode')
        if mode == self.ADJUST:
            if not cleaned.get('year'):
                self.add_error('year', 'Required for adjust mode.')
        elif mode == self.CLONE:
            if not cleaned.get('source_year'):
                self.add_error('source_year', 'Required for clone mode.')
            if not cleaned.get('target_year'):
                self.add_error('target_year', 'Required for clone mode.')
            elif cleaned.get('source_year') and cleaned['source_year'] == cleaned['target_year']:
                self.add_error('target_year', 'Target year must differ from the source year.')
        return cleaned


def _scale_rate(rate, percent):
    factor = Decimal('1') + (Decimal(percent) / Decimal('100'))
    return (Decimal(rate) * factor).quantize(Decimal('1'), rounding=ROUND_HALF_UP).quantize(Decimal('0.01'))


def _build_price_bulk_plan(data):
    """Compute what an adjust/clone operation would do, without writing anything.

    Returns a dict with 'mode', 'updates' (existing Price rows + their new rate, adjust mode),
    'creates' (unsaved Price instances, clone mode) and 'skipped' (clone rows whose shifted dates
    already overlap an existing row in the target year).
    """
    percent = data['percent']
    properties = list(data['properties']) or None
    plan = {'mode': data['mode'], 'updates': [], 'creates': [], 'skipped': []}

    if data['mode'] == PriceBulkToolForm.ADJUST:
        qs = Price.objects.filter(start_date__year=data['year']).select_related('property')
        if properties:
            qs = qs.filter(property__in=properties)
        for price in qs.order_by('property__title', 'start_date'):
            plan['updates'].append({
                'price': price,
                'old_rate': price.rate,
                'new_rate': _scale_rate(price.rate, percent),
            })
    else:
        offset = data['target_year'] - data['source_year']
        qs = Price.objects.filter(start_date__year=data['source_year']).select_related('property')
        if properties:
            qs = qs.filter(property__in=properties)
        for price in qs.order_by('property__title', 'start_date'):
            new_start = price.start_date.replace(year=price.start_date.year + offset)
            new_end = price.end_date.replace(year=price.end_date.year + offset)
            if Price.overlapping(price.property_id, new_start, new_end).exists():
                plan['skipped'].append({'price': price, 'new_start': new_start, 'new_end': new_end})
                continue
            new_price = Price(
                property=price.property,
                start_date=new_start,
                end_date=new_end,
                rate=_scale_rate(price.rate, percent),
                weekly_discount_percent=price.weekly_discount_percent,
                last_minute_discount_percent=price.last_minute_discount_percent,
                last_minute_discount_days=price.last_minute_discount_days,
                monthly_discount_percent=price.monthly_discount_percent,
                extra_adult_rate=price.extra_adult_rate,
                extra_child_rate=price.extra_child_rate,
            )
            plan['creates'].append({'price': new_price, 'old_rate': price.rate, 'new_rate': new_price.rate})
    return plan


def _apply_price_bulk_plan(plan):
    if plan['mode'] == PriceBulkToolForm.ADJUST:
        rows = [u['price'] for u in plan['updates']]
        for row, u in zip(rows, plan['updates']):
            row.rate = u['new_rate']
        Price.objects.bulk_update(rows, ['rate'], batch_size=200)
        return len(rows)
    else:
        Price.objects.bulk_create([c['price'] for c in plan['creates']], batch_size=200)
        return len(plan['creates'])


class PriceAdmin(admin.ModelAdmin):
    """Landing page lists properties; clicking one filters to that property's editable price rows."""
    list_display = (
        'start_date', 'end_date', 'rate',
        'weekly_discount_percent',
        'monthly_discount_percent',
        'last_minute_discount_percent', 'last_minute_discount_days',
        'extra_adult_rate', 'extra_child_rate',
    )
    list_editable = (
        'start_date', 'end_date', 'rate',
        'weekly_discount_percent',
        'monthly_discount_percent',
        'last_minute_discount_percent', 'last_minute_discount_days',
        'extra_adult_rate', 'extra_child_rate',
    )
    list_filter = ('property',)
    ordering = ('start_date',)
    list_display_links = None  # every list_display column is also list_editable now that 'name'
                                # (the old link column) is gone - fully inline-editable instead

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name in ('start_date', 'end_date'):
            formfield.widget.attrs['placeholder'] = 'DD-MM-YYYY'
        if db_field.name in NARROW_FIELD_WIDTHS:
            formfield.widget.attrs['style'] = f'width: {NARROW_FIELD_WIDTHS[db_field.name]}'
        return formfield

    def get_changeform_initial_data(self, request):
        """Pre-select the property on the add form when arriving from a filtered price list."""
        initial = super().get_changeform_initial_data(request)
        changelist_filters = request.GET.get('_changelist_filters')
        if changelist_filters and 'property' not in initial:
            filters = dict(parse_qsl(changelist_filters))
            if 'property__id__exact' in filters:
                initial['property'] = filters['property__id__exact']
        return initial

    def changelist_view(self, request, extra_context=None):
        if 'property__id__exact' not in request.GET:
            return self.property_picker_view(request)
        return super().changelist_view(request, extra_context)

    def property_picker_view(self, request):
        context = {
            **self.admin_site.each_context(request),
            'title': 'Select a property',
            'properties': Property.objects.order_by('title'),
            'changelist_url': reverse('admin:properties_price_changelist'),
            'bulk_tools_url': reverse('admin:properties_price_bulk_tools'),
        }
        return TemplateResponse(request, 'admin/properties/price_property_picker.html', context)

    def get_urls(self):
        return [
            path('bulk-tools/', self.admin_site.admin_view(self.bulk_tools_view),
                 name='properties_price_bulk_tools'),
        ] + super().get_urls()

    def bulk_tools_view(self, request):
        if not (self.has_add_permission(request) and self.has_change_permission(request)):
            raise PermissionDenied
        context = {
            **self.admin_site.each_context(request),
            'title': 'Bulk price tools',
            'picker_url': reverse('admin:properties_price_changelist'),
        }
        confirming = request.POST.get('step') == 'confirm'
        if request.method == 'POST':
            form = PriceBulkToolForm(request.POST)
            if form.is_valid():
                plan = _build_price_bulk_plan(form.cleaned_data)
                if confirming:
                    count = _apply_price_bulk_plan(plan)
                    verb = 'Updated' if plan['mode'] == PriceBulkToolForm.ADJUST else 'Created'
                    skipped_note = f" ({len(plan['skipped'])} skipped - already priced in the target year)" if plan['skipped'] else ''
                    messages.success(request, f"{verb} {count} price rows.{skipped_note}")
                    return HttpResponseRedirect(reverse('admin:properties_price_bulk_tools'))
                context['plan'] = plan
                context['preview_rows'] = (plan['updates'] or plan['creates'])[:100]
                context['preview_truncated'] = len(plan['updates'] or plan['creates']) > 100
            context['form'] = form
        else:
            context['form'] = PriceBulkToolForm()
        return TemplateResponse(request, 'admin/properties/price_bulk_tools.html', context)


class LocationAdmin(admin.ModelAdmin):
    inlines = [LocationSpecInline, LocationImageInline, LocationRulesInline]


admin.site.register(Property, PropertyAdmin)
admin.site.register(Price, PriceAdmin)
admin.site.register(Location, LocationAdmin)
admin.site.register(Owner)
admin.site.register(Accountant)
admin.site.register(ManagementCompany)