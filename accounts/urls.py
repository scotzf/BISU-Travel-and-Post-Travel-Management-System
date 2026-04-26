from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    # Public routes
    path('', views.home, name='home'),
    path('login/', views.login, name='login'),
    path('register/', views.register, name='register'),
    path('logout/', views.logout, name='logout'),
    path('profile/', views.profile, name='profile'),
    path('profile/update/', views.update_profile, name='update_profile'),
    
    # User dashboard
    path('dashboard/', views.dashboard, name='dashboard'),
    
    # Admin routes - CHANGED PATH to avoid conflict with Django admin
    path('approvals/', views.pending_approvals, name='pending_approvals'),
    path('approvals/approve/<int:user_id>/', views.approve_user, name='approve_user'),
    path('approvals/reject/<int:user_id>/', views.reject_user, name='reject_user'),
    path('approvals/user-details/<int:user_id>/', views.view_user_details, name='view_user_details'),

    path('invite/<uuid:token>/', views.invite_register, name='invite_register'),
]