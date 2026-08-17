from django.urls import path

from . import views

app_name = 'bookings'

urlpatterns = [
    path('manage/', views.ManageBookingView.as_view(), name='manage'),
    path('<str:reference>/', views.BookingConfirmationView.as_view(), name='confirmation'),
]
