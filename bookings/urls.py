from django.urls import path

from . import views

app_name = 'bookings'

urlpatterns = [
    path('manage/', views.ManageBookingView.as_view(), name='manage'),
    path('conditions/', views.BookingConditionsView.as_view(), name='conditions'),
    path('<str:reference>/details/', views.BookingDetailsView.as_view(), name='details'),
    path('<str:reference>/pay/', views.BookingPaymentView.as_view(), name='pay'),
    path('<str:reference>/pay/status/', views.BookingPaymentStatusView.as_view(), name='pay_status'),
    path('<str:reference>/pay/cancel/', views.BookingPaymentCancelView.as_view(), name='pay_cancel'),
    path('<str:reference>/balance/', views.BookingBalanceDetailsView.as_view(), name='balance_details'),
    path('<str:reference>/balance/pay/', views.BookingBalancePaymentView.as_view(), name='balance_pay'),
    path('<str:reference>/manage/', views.BookingManageHubView.as_view(), name='manage_hub'),
    path('<str:reference>/manage/guests/', views.BookingManageGuestsView.as_view(), name='manage_guests'),
    path('<str:reference>/manage/guests/add/', views.BookingManageGuestAddView.as_view(), name='manage_hub_guest_add'),
    path('<str:reference>/manage/guests/remove/', views.BookingManageGuestRemoveView.as_view(), name='manage_hub_guest_remove'),
    path('<str:reference>/manage/arrival-departure/', views.BookingManageArrivalDepartureView.as_view(), name='manage_arrival_departure'),
    path('<str:reference>/manage/extras/', views.BookingManageExtrasView.as_view(), name='manage_extras'),
    path('<str:reference>/manage/cancel/', views.BookingCancelView.as_view(), name='manage_cancel'),
    path('<str:reference>/manage/amenities/', views.BookingManageAmenitiesView.as_view(), name='manage_amenities'),
    path('<str:reference>/manage/location/', views.BookingManageLocationView.as_view(), name='manage_location'),
    path('<str:reference>/manage/faq/', views.BookingManageFAQView.as_view(), name='manage_faq'),
    path('<str:reference>/', views.BookingConfirmationView.as_view(), name='confirmation'),  # catch-all, must stay LAST
]
