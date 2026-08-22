from django.urls import path

from . import views

app_name = 'staff'
urlpatterns = [
    path('', views.StaffHomeView.as_view(), name='home'),
    path('bookings/', views.StaffBookingLookupView.as_view(), name='booking_lookup'),
    path('guests/', views.StaffGuestListView.as_view(), name='guest_list'),
    path('guests/<int:pk>/', views.StaffGuestDetailView.as_view(), name='guest_detail'),
    path('bookings/<str:reference>/', views.StaffBookingDetailView.as_view(), name='booking_detail'),
]
