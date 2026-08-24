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
    path('locations/<int:pk>/', views.StaffLocationDetailView.as_view(), name='location_detail'),
    path('bookings/<str:reference>/', views.StaffBookingDetailView.as_view(), name='booking_detail'),
]
