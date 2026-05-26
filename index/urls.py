from django.urls import path
from . import views

urlpatterns = [
    path('', views.IndexView.as_view(), name='index'),
    path('_index', views._IndexView.as_view(), name='_index'),
]