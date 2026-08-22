from django.urls import path
from . import views

app_name = 'properties'
urlpatterns = [
    path('calendar/<str:token>.ics', views.PropertyCalendarExportView.as_view(), name='calendar_export'),
    path('<str:title>/', views.LocationView.as_view(), name='location/page'),
    path('<str:location>/<str:title>/', views.PropertyView.as_view(), name='property/page'),
    path('<str:location>/<str:title>/reserve/', views.ReserveView.as_view(), name='property/reserve'),
]