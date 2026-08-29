from django.urls import path

from . import views

app_name = 'staff'
urlpatterns = [
    path('', views.StaffHomeView.as_view(), name='home'),
    path('bookings/', views.StaffBookingLookupView.as_view(), name='booking_lookup'),
    path('guests/', views.StaffGuestListView.as_view(), name='guest_list'),
    path('guests/<int:pk>/', views.StaffGuestDetailView.as_view(), name='guest_detail'),
    path('properties/', views.StaffPropertyListView.as_view(), name='property_list'),
    path('properties/new/', views.StaffPropertyCreateView.as_view(), name='property_create'),
    path('locations/', views.StaffLocationListView.as_view(), name='location_list'),
    path('locations/new/', views.StaffLocationCreateView.as_view(), name='location_create'),
    path('quick-add/<str:model>/', views.StaffQuickAddView.as_view(), name='quick_add'),
    path('settings/', views.StaffSettingsView.as_view(), name='settings'),
    path('properties/<int:pk>/', views.StaffPropertyDetailView.as_view(), name='property_detail'),
    path(
        'properties/<int:pk>/ical/<int:link_id>/sync/',
        views.StaffIcalSyncView.as_view(), name='ical_sync',
    ),
    path('locations/<int:pk>/', views.StaffLocationDetailView.as_view(), name='location_detail'),
    path('bookings/<str:reference>/', views.StaffBookingDetailView.as_view(), name='booking_detail'),
    path('cleaning/', views.StaffCleaningRotaView.as_view(), name='cleaning_rota'),
    path('cleaning/calendar/', views.StaffCleaningCalendarView.as_view(), name='cleaning_calendar'),
    path(
        'cleaning/calendar/events/',
        views.StaffCleaningEventsView.as_view(), name='cleaning_calendar_events',
    ),
    path(
        'cleaning/calendar/tasks/<int:pk>/move/',
        views.StaffCleaningTaskMoveView.as_view(), name='cleaning_calendar_move',
    ),
    path(
        'cleaning/tasks/<int:pk>/detail/',
        views.StaffCleaningTaskDetailView.as_view(), name='cleaning_task_detail',
    ),
    path(
        'cleaning/tasks/<int:pk>/save/',
        views.StaffCleaningTaskSaveView.as_view(), name='cleaning_task_save',
    ),
    path(
        'cleaning/tasks/<int:pk>/dismiss/',
        views.StaffCleaningTaskDismissView.as_view(), name='cleaning_task_dismiss',
    ),
    path('checkins/', views.StaffCheckinCalendarView.as_view(), name='checkins_calendar'),
    path(
        'checkins/events/',
        views.StaffCheckinEventsView.as_view(), name='checkins_calendar_events',
    ),
    path(
        'checkins/<int:pk>/move/',
        views.StaffCheckinMoveView.as_view(), name='checkins_calendar_move',
    ),
    path('checkins/<int:pk>/detail/', views.StaffCheckinDetailView.as_view(), name='checkin_detail'),
    path(
        'checkins/<int:pk>/toggle-done/',
        views.StaffCheckinToggleDoneView.as_view(), name='checkin_toggle_done',
    ),
    path('checkins/<int:pk>/save/', views.StaffCheckinSaveView.as_view(), name='checkin_save'),
    path('finance/memos/', views.StaffFinanceMemosView.as_view(), name='finance_memos'),
    path('finance/memos/<int:pk>/', views.StaffFinanceMemoDetailView.as_view(), name='finance_memo_detail'),
    path('finance/memos/<int:pk>/send/', views.StaffFinanceMemoSendView.as_view(), name='finance_memo_send'),
    path(
        'finance/services/',
        views.StaffFinanceAdHocServiceListView.as_view(), name='finance_ad_hoc_services',
    ),
    path('finance/payouts/', views.StaffFinancePayoutsView.as_view(), name='finance_payouts'),
    path(
        'finance/payouts/<str:reference>/mark-paid/',
        views.StaffFinancePayoutMarkPaidView.as_view(), name='finance_payout_mark_paid',
    ),
    path('finance/statement/', views.StaffFinanceStatementView.as_view(), name='finance_statement'),
]
