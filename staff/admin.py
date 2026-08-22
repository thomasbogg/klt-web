from django.contrib import admin

from staff.models import Deduction, OwnerPayment, TaskHistoryEntry

admin.site.register(Deduction)
admin.site.register(OwnerPayment)
admin.site.register(TaskHistoryEntry)
