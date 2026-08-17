from django import forms
from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import reverse

EU_DATE_FORMAT = '%d-%m-%Y'

NARROW_FIELD_WIDTHS = {
    'start_date': '7em',
    'end_date': '7em',
    'rate': '5.5em',
    'weekly_discount_percent': '4.5em',
    'monthly_discount_percent': '4.5em',
    'last_minute_discount_percent': '4.5em',
}

# Register your models here.
from .models import (
    Property, Price, Location, Owner, Manager, Accountant,
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


class PropertyAdmin(admin.ModelAdmin):
    inlines = [SpecInline, AmenityInline, SEFDetailInline, iCalLinkInline, PropertyImageInline]


class PriceAdmin(admin.ModelAdmin):
    """Landing page lists properties; clicking one filters to that property's editable price rows."""
    list_display = (
        'name', 'start_date', 'end_date', 'rate',
        'weekly_discount_percent',
        'monthly_discount_percent', 'monthly_discount_min_nights',
        'last_minute_discount_percent', 'last_minute_discount_days',
    )
    list_editable = (
        'start_date', 'end_date', 'rate',
        'weekly_discount_percent',
        'monthly_discount_percent', 'monthly_discount_min_nights',
        'last_minute_discount_percent', 'last_minute_discount_days',
    )
    list_filter = ('property',)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name in ('start_date', 'end_date'):
            formfield.widget = forms.DateInput(format=EU_DATE_FORMAT, attrs={'placeholder': 'DD-MM-YYYY'})
            formfield.input_formats = [EU_DATE_FORMAT]
        if db_field.name in NARROW_FIELD_WIDTHS:
            formfield.widget.attrs['style'] = f'width: {NARROW_FIELD_WIDTHS[db_field.name]}'
        return formfield

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
admin.site.register(Manager)
admin.site.register(Accountant)