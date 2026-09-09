from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = 'owners'
urlpatterns = [
    path('login/', views.OwnerLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='owners:login'), name='logout'),
    path('invite/<uidb64>/<token>/', views.OwnerAcceptInviteView.as_view(), name='accept_invite'),
    path('', views.OwnerHomeView.as_view(), name='home'),
    path('contact-details/', views.OwnerContactDetailsView.as_view(), name='contact_details'),
    path('calendar-links/', views.OwnerCalendarLinksView.as_view(), name='calendar_links'),
    path('reports/', views.OwnerReportView.as_view(), name='reports'),
    path('calendar/', views.OwnerCalendarView.as_view(), name='calendar'),
    path('payouts-memos/', views.OwnerPayoutsMemosView.as_view(), name='payouts_memos'),
    path('payouts-memos/memo/<int:pk>/', views.OwnerMemoDetailView.as_view(), name='memo_detail'),
    path('bookings/', views.OwnerBookingsListView.as_view(), name='bookings'),
    path('bookings/new/', views.OwnerBookingCreateView.as_view(), name='booking_create'),
    path('bookings/<str:reference>/', views.OwnerBookingDetailView.as_view(), name='booking_detail'),
]
