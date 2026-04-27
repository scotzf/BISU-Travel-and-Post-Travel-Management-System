from django.db.models import Q


def secretary_queue_count(request):
    if not request.session.get('user_id'):
        return {}

    role = request.session.get('role')
    if role not in ['DEPT_SEC', 'CAMPUS_SEC']:
        return {}

    try:
        from .models import TravelRecord
        from accounts.models import User

        user = User.objects.get(id=request.session['user_id'])

        if role == 'DEPT_SEC' and user.college:
            own = TravelRecord.objects.filter(
                scope='COLLEGE',
                budget_source__isnull=True,
                participants__college_name=user.college.name  # FIXED: was college_snapshot
            ).distinct().count()
            routed = TravelRecord.objects.filter(
                scope='CAMPUS',
                budget_source__isnull=True,
                funding_college=user.college
            ).distinct().count()
            count = own + routed

        elif role == 'CAMPUS_SEC' and user.campus:
            count = TravelRecord.objects.filter(
                scope='CAMPUS',
                budget_source__isnull=True,
                funding_college__isnull=True,
                participants__campus_name=user.campus.name  # FIXED: was campus_snapshot
            ).distinct().count()
        else:
            count = 0

        return {'secretary_queue_count': count}

    except Exception:
        return {}


def unread_notifications(request):
    """
    Injects unread_notifications (count) and recent_notifications (list)
    into every template context for the logged-in user.
    """
    if not request.session.get('user_id'):
        return {}

    try:
        from .models import Notification
        from accounts.models import User

        user = User.objects.get(id=request.session['user_id'])

        unread = Notification.objects.filter(user=user, is_read=False)
        count  = unread.count()

        # Send the 6 most recent unread for the dropdown
        recent = unread.select_related('travel_record').order_by('-created_at')[:6]

        return {
            'unread_notifications':  count,
            'recent_notifications':  recent,
        }

    except Exception:
        return {}