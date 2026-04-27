from django.urls import path
from . import views

app_name = 'travel_app'

urlpatterns = [
    # ── Employee ──────────────────────────────────────────────────────
    path('employee/',               views.employee_dashboard,         name='employee_dashboard'),
    path('employee/travels/',       views.my_travels,                 name='my_travels'),
    path('employee/stats/',         views.my_stats,                   name='my_stats'),

    # ── Secretary ─────────────────────────────────────────────────────
    path('dept-secretary/',         views.dept_secretary_dashboard,   name='dept_secretary_dashboard'),
    path('campus-secretary/',       views.campus_secretary_dashboard, name='campus_secretary_dashboard'),
    path('secretary/queue/',        views.secretary_queue,            name='secretary_queue'),

    # ── Admin ─────────────────────────────────────────────────────────
    path('admin-panel/',            views.admin_dashboard,            name='admin_dashboard'),

    # ── Budget sources (admin + secretaries) ──────────────────────────
    path('budget/sources/',         views.manage_budget_sources,      name='manage_budget_sources'),

    # ── Travel records ────────────────────────────────────────────────
    path('travels/',                views.all_travels,                name='all_travels'),
    path('travels/extract-travel-order/', views.extract_travel_order_ajax, name='extract_travel_order_ajax'),
    path('travels/lookup-traveler/', views.lookup_traveler_ajax,      name='lookup_traveler_ajax'),
    path('travels/new/',            views.create_travel,              name='create_travel'),
    path('travels/<int:pk>/',       views.travel_detail,              name='travel_detail'),
    path('travels/<int:pk>/upload/',views.upload_document,            name='upload_document'),
    path('travels/<int:pk>/budget/',views.tag_budget,                 name='tag_budget'),
    path('travels/<int:pk>/download-zip/', views.download_zip,        name='download_zip'),
    path('travels/<int:pk>/change-scope/', views.change_scope,        name='change_scope'),

    # ── Documents ─────────────────────────────────────────────────────
    path('documents/<int:doc_id>/confirm/',    views.confirm_extraction,   name='confirm_extraction'),
    path('documents/<int:doc_id>/reject/',     views.reject_extraction,    name='reject_extraction'),
    path('documents/<int:doc_id>/replace/',    views.replace_document,     name='replace_document'),
    path('documents/<int:doc_id>/set-amount/', views.set_document_amount,  name='set_document_amount'),

    # ── Budget & overview ─────────────────────────────────────────────
    path('budget/',                 views.budget_overview,            name='budget_overview'),
    path('liquidation/',            views.liquidation_calculator,     name='liquidation_calculator'),

    # ── Event groups ──────────────────────────────────────────────────
    path('events/',                          views.event_groups,              name='event_groups'),
    path('events/create/',                   views.create_event_group,        name='create_event_group'),
    path('events/<int:pk>/',                 views.event_group_detail,        name='event_group_detail'),
    path('events/<int:pk>/edit/',            views.edit_event_group,          name='edit_event_group'),
    path('events/<int:pk>/delete/',          views.delete_event_group,        name='delete_event_group'),
    path('events/<int:pk>/unlink/<int:travel_pk>/', views.unlink_travel_from_group, name='unlink_travel_from_group'),
    path('events/<int:pk>/add-travel/',      views.add_travel_to_group,       name='add_travel_to_group'),

    # ── Reports ───────────────────────────────────────────────────────
    path('stats/',                  views.stats_view,                 name='stats'),


    path('travels/<int:pk>/invite/', views.invite_participant, name='invite_participant'),
    # Notifications
    path('notifications/',                    views.notifications_list,          name='notifications_list'),
    path('notifications/<int:notif_id>/read/', views.mark_notification_read,      name='mark_notification_read'),
    path('notifications/mark-all-read/',       views.mark_all_notifications_read, name='mark_all_notifications_read'),
 
]