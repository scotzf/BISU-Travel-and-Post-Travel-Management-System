from django.urls import path
from . import views

app_name = 'travel_app'

urlpatterns = [
    # Employee URLs
    path('employee/dashboard/', views.employee_dashboard, name='employee_dashboard'),
    path('employee/create/', views.create_travel, name='create_travel'),
    path('employee/history/', views.travel_history, name='travel_history'),
  #  path('employee/travel/<int:travel_id>/', views.travel_detail, name='travel_detail'),
    path('employee/travel/<int:travel_id>/edit-budget/', views.edit_travel_budget, name='edit_travel_budget'),
    path('employee/travel/<int:travel_id>/edit-details/', views.edit_travel_details, name='edit_travel_details'),
    path('employee/travel/<int:travel_id>/upload-document/', views.upload_post_travel_document, name='upload_post_travel_document'),
    path('employee/travel/<int:travel_id>/upload-financial/', views.upload_financial_document, name='upload_financial_document'),
    path('employee/travel/<int:travel_id>/submit-liquidation/', views.submit_liquidation, name='submit_liquidation'),
    path('employee/notifications/', views.notifications, name='notifications'),
    path('employee/notification/<int:notif_id>/mark-read/', views.mark_notification_read, name='mark_notification_read'),
    
    # Secretary URLs (Placeholder)
    path('secretary/dashboard/', views.secretary_dashboard, name='secretary_dashboard'),
    
    # Dean URLs (Placeholder)
    path('dean/', views.dean_dashboard, name='dean_dashboard'),
    path('dean/approve/', views.dean_approve, name='dean_approve'),
    path('dean/batch-approve/', views.dean_batch_approve, name='dean_batch_approve'),
    path('dean/employee-history/<int:employee_id>/', views.dean_employee_history, name='dean_employee_history'),
    path('dean/history/', views.approval_history, name='approval_history'),
    path('dean/notifications/', views.dean_notifications, name='dean_notifications'),
    path('dean/comment/<int:travel_order_id>/', views.add_comment, name='add_comment'),
    path('dean/create-travel/', views.dean_create_travel, name='dean_create_travel'),
#   # The travel_detail url should remain the same but the template path changes:
    path('travel/<int:travel_id>/', views.travel_detail, name='travel_detail'),
    path('dean/travel-history/', views.dean_travel_history, name='dean_travel_history'),
    path('dean/notifications/', views.dean_notifications, name='dean_notifications'),
    path('dean/notifications/mark-all-read/', views.dean_mark_all_read, name='dean_mark_all_read'),
    path('dean/notifications/<int:notif_id>/mark-read/', views.dean_mark_notif_read, name='dean_mark_notif_read'),
    path('dean/reports/', views.dean_reports, name='dean_reports'),
    path('dean/reports/export/', views.export_dean_report, name='export_dean_report'),
    
    # Director URLs (Placeholder)
    path('director/dashboard/', views.director_dashboard, name='director_dashboard'),
    
    # President URLs (Placeholder)
    path('president/dashboard/', views.president_dashboard, name='president_dashboard'),
    
    # Budget Officer URLs (Placeholder)
    path('budget/dashboard/', views.budget_dashboard, name='budget_dashboard'),
    
    # Cashier URLs (Placeholder)
    path('cashier/dashboard/', views.cashier_dashboard, name='cashier_dashboard'),
]