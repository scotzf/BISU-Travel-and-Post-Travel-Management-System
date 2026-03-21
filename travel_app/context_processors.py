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
                participants__college_snapshot=user.college.name
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
                participants__campus_snapshot=user.campus.name
            ).distinct().count()
        else:
            count = 0

        return {'secretary_queue_count': count}

    except Exception:
        return {}