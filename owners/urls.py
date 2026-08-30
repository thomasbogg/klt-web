from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = 'owners'
urlpatterns = [
    path('login/', views.OwnerLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='owners:login'), name='logout'),
    path('', views.OwnerHomeView.as_view(), name='home'),
    path('reports/', views.OwnerReportView.as_view(), name='reports'),
    path('calendar/', views.OwnerCalendarView.as_view(), name='calendar'),
    path('bookings/', views.OwnerBookingsListView.as_view(), name='bookings'),
    path('bookings/new/', views.OwnerBookingCreateView.as_view(), name='booking_create'),
    path('bookings/<str:reference>/', views.OwnerBookingDetailView.as_view(), name='booking_detail'),
]
