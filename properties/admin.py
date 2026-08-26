from urllib.parse import parse_qsl

from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import reverse

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
        }
        return TemplateResponse(request, 'admin/properties/price_property_picker.html', context)


class LocationAdmin(admin.ModelAdmin):
    inlines = [LocationSpecInline, LocationImageInline, LocationRulesInline]


admin.site.register(Property, PropertyAdmin)
admin.site.register(Price, PriceAdmin)
admin.site.register(Location, LocationAdmin)
admin.site.register(Owner)
admin.site.register(Accountant)
admin.site.register(ManagementCompany)