from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = 'accountants'
urlpatterns = [
    path('login/', views.AccountantLoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='accountants:login'), name='logout'),
    path('invite/<uidb64>/<token>/', views.AccountantAcceptInviteView.as_view(), name='accept_invite'),
    path('', views.AccountantHomeView.as_view(), name='home'),
    path('reports/', views.AccountantReportView.as_view(), name='reports'),
]
