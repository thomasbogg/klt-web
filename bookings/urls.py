from django.urls import path

from . import views

app_name = 'bookings'

urlpatterns = [
    path('manage/', views.ManageBookingView.as_view(), name='manage'),
    path('conditions/', views.BookingConditionsView.as_view(), name='conditions'),
    path('<str:reference>/pay/', views.BookingPaymentView.as_view(), name='pay'),
    path('<str:reference>/pay/status/', views.BookingPaymentStatusView.as_view(), name='pay_status'),
    path('<str:reference>/pay/cancel/', views.BookingPaymentCancelView.as_view(), name='pay_cancel'),
    path('<str:reference>/', views.BookingConfirmationView.as_view(), name='confirmation'),  # catch-all, must stay LAST
]
