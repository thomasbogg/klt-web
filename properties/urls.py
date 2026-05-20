from django.urls import path
from . import views

app_name = 'properties'
urlpatterns = [
    path('<str:title>/', views.location, name='location'),
    path('<str:location>/<str:title>/', views.property, name='property'),
]