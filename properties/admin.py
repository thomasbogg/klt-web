from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import reverse

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