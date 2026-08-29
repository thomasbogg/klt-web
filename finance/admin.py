from django.contrib import admin

from finance.models import AdHocService, Memo, PayoutRecord

admin.site.register(Memo)
admin.site.register(AdHocService)
admin.site.register(PayoutRecord)
