from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = 'staff'
urlpatterns = [
    # Dedicated bespoke staff login (2026-09-06), replacing the generic Django admin login page
    # every staff sign-in used until now - resolves the "unified vs. separate staff/owner login
    # pages" decision this had flagged as deferred. staff.permissions' staff_member_required calls
    # now point their login_url at this instead of the default 'admin:login'.
    path('login/', views.StaffLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='staff:login'), name='logout'),
    path('invite/<uidb64>/<token>/', views.StaffAcceptInviteView.as_view(), name='accept_invite'),
    path('', views.StaffHomeView.as_view(), name='home'),
    path('bookings/', views.StaffBookingLookupView.as_view(), name='booking_lookup'),
    path('bookings/new/owner/', views.StaffOwnerBookingCreateView.as_view(), name='booking_create_owner'),
    path('bookings/new/offer/', views.StaffGuestOfferCreateView.as_view(), name='booking_create_offer'),
    path('bookings/new/offer/guest-search/', views.StaffGuestSearchView.as_view(), name='booking_guest_search'),
    path('guests/', views.StaffGuestListView.as_view(), name='guest_list'),
    path('guests/<int:pk>/', views.StaffGuestDetailView.as_view(), name='guest_detail'),
    path('properties/', views.StaffPropertyListView.as_view(), name='property_list'),
    path('properties/new/', views.StaffPropertyCreateView.as_view(), name='property_create'),
    path('properties/bulk-prices/', views.StaffPriceBulkToolsView.as_view(), name='property_bulk_prices'),
    path('locations/', views.StaffLocationListView.as_view(), name='location_list'),
    path('locations/new/', views.StaffLocationCreateView.as_view(), name='location_create'),
    path('quick-add/<str:model>/', views.StaffQuickAddView.as_view(), name='quick_add'),
    path('settings/', views.StaffSettingsView.as_view(), name='settings'),
    path('settings/sage/connect/', views.StaffSageConnectView.as_view(), name='sage_connect'),
    path('properties/<int:pk>/', views.StaffPropertyDetailView.as_view(), name='property_detail'),
    path(
        'properties/<int:pk>/platform-rates/',
        views.StaffPropertyPlatformRatesView.as_view(), name='property_platform_rates',
    ),
    path(
        'properties/<int:pk>/ical/<int:link_id>/sync/',
        views.StaffIcalSyncView.as_view(), name='ical_sync',
    ),
    path('locations/<int:pk>/', views.StaffLocationDetailView.as_view(), name='location_detail'),
    path('bookings/<str:reference>/', views.StaffBookingDetailView.as_view(), name='booking_detail'),
    path(
        'bookings/<str:reference>/emails/<int:pk>/send/',
        views.StaffBookingEmailSendView.as_view(), name='booking_email_send',
    ),
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
        'finance/memos/<int:pk>/toggle-management-fee-paid/',
        views.StaffFinanceMemoManagementFeePaidView.as_view(), name='finance_memo_toggle_management_fee_paid',
    ),
    path(
        'finance/services/',
        views.StaffFinanceAdHocServiceListView.as_view(), name='finance_ad_hoc_services',
    ),
    path('finance/payouts/', views.StaffFinancePayoutsView.as_view(), name='finance_payouts'),
    path(
        'finance/payouts/<str:reference>/mark-paid/',
        views.StaffFinancePayoutMarkPaidView.as_view(), name='finance_payout_mark_paid',
    ),
    path(
        'finance/payouts/month-end/<int:owner_id>/generate/',
        views.StaffFinanceOwnerPayoutGenerateView.as_view(), name='finance_owner_payout_generate',
    ),
    path('finance/deposits/', views.StaffFinanceDepositsView.as_view(), name='finance_deposits'),
    path(
        'finance/deposits/<str:reference>/mark-returned/',
        views.StaffFinanceDepositReturnMarkReturnedView.as_view(), name='finance_deposit_mark_returned',
    ),
    path('finance/statement/', views.StaffFinanceStatementView.as_view(), name='finance_statement'),
    path(
        'finance/expected-payments/',
        views.StaffFinanceExpectedPaymentsView.as_view(), name='finance_expected_payments',
    ),
    path(
        'finance/expected-payments/<int:owner_id>/consolidate/',
        views.StaffFinanceConsolidateInformalCleansView.as_view(), name='finance_consolidate_informal_cleans',
    ),
    path(
        'finance/owner-invoices/<int:pk>/retry/',
        views.StaffFinanceOwnerInvoiceRetryView.as_view(), name='finance_owner_invoice_retry',
    ),
    path(
        'finance/owner-invoices/<int:pk>/mark-paid/',
        views.StaffFinanceOwnerInvoiceMarkPaidView.as_view(), name='finance_owner_invoice_mark_paid',
    ),
    path('reports/', views.StaffReportsView.as_view(), name='reports'),
    path('reports/monthly/', views.StaffReportsMonthlyView.as_view(), name='reports_monthly'),
    path('reports/stays/', views.StaffReportsStaysView.as_view(), name='reports_stays'),
    path('reports/enquiries/', views.StaffReportsEnquiriesView.as_view(), name='reports_enquiries'),
    path('reports/extras/', views.StaffReportsExtrasView.as_view(), name='reports_extras'),
    path('reports/commissions/', views.StaffReportsCommissionsView.as_view(), name='reports_commissions'),
    path('reports/management/', views.StaffReportsManagementView.as_view(), name='reports_management'),
]
