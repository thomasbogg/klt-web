from django.contrib import admin

# Register your models here.
from .models import Property, Price, Location, Owner, Manager, Accountant

admin.site.register(Property)
admin.site.register(Price)
admin.site.register(Location)
admin.site.register(Owner)
admin.site.register(Manager)
admin.site.register(Accountant)