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
    path('<str:reference>/', views.BookingConfirmationView.as_view(), name='confirmation'),  # catch-all, must stay LAST
]
