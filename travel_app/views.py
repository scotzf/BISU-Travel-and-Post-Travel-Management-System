from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.utils import timezone
from django.http import JsonResponse
from django.db import transaction
from accounts.views import get_authenticated_user
from accounts.models import User
from .models import (
    TravelRecord, TravelDocument, TravelParticipant,
    BudgetSource, BudgetUsage, CampusBudgetUsage, EventGroup, Notification
)
from .budget_service import get_sources_for_secretary


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def require_role(roles):
    def decorator(view_func):
        def wrapper(request, *args, **kwargs):
            user = get_authenticated_user(request)
            if not user:
                return redirect('accounts:login')
            if user.role not in roles:
                return redirect('accounts:dashboard')
            return view_func(request, *args, user=user, **kwargs)
        wrapper.__name__ = view_func.__name__
        return wrapper
    return decorator


def _travel_stats_for_queryset(qs):
    today = timezone.now().date()
    travels = list(qs)
    return {
        'total_travels':      len(travels),
        'completed_travels':  sum(1 for t in travels if t.end_date and t.end_date < today),
        'upcoming_travels':   sum(1 for t in travels if t.start_date > today),
        'incomplete_travels': sum(1 for t in travels if t.completeness_percentage < 100),
    }


def _detect_duplicates(travels_qs):
    travels = list(travels_qs.filter(event_group__isnull=True).order_by('destination', 'start_date'))
    alerts, seen = [], set()
    for i, a in enumerate(travels):
        for b in travels[i+1:]:
            if len(alerts) >= 5:
                break
            if a.destination.lower() != b.destination.lower():
                continue
            a_end = a.end_date or a.start_date
            b_end = b.end_date or b.start_date
            if a.start_date <= b_end and b.start_date <= a_end:
                key = tuple(sorted([a.id, b.id]))
                if key not in seen:
                    seen.add(key)
                    alerts.append((a, b))
    return alerts


def _notify_if_duplicate(travel, creator):
    from django.db.models import Q
    a_end = travel.end_date or travel.start_date
    duplicates = TravelRecord.objects.filter(
        destination__iexact=travel.destination
    ).filter(
        Q(start_date__lte=a_end) & Q(end_date__gte=travel.start_date) |
        Q(start_date__lte=a_end) & Q(end_date__isnull=True, start_date__gte=travel.start_date)
    ).exclude(id=travel.id)

    if not duplicates.exists():
        return

    if travel.scope == 'COLLEGE' and creator.college:
        secretaries = User.objects.filter(role='DEPT_SEC', college=creator.college, is_active=True)
    else:
        secretaries = User.objects.filter(role='CAMPUS_SEC', campus=creator.campus, is_active=True)

    for sec in secretaries:
        Notification.objects.create(
            user=sec,
            notification_type='DUPLICATE_DETECTED',
            title='Possible duplicate travel detected',
            message=(
                f'A new travel to {travel.destination} '
                f'({travel.start_date}) may overlap with an existing record. '
                f'Consider linking them as an event group.'
            ),
            travel_record=travel,
        )


# ══════════════════════════════════════════════════════════════════════
# EMPLOYEE DASHBOARD
# ══════════════════════════════════════════════════════════════════════

@never_cache
@require_role(['EMPLOYEE'])
def employee_dashboard(request, user=None):
    my_travels = TravelRecord.objects.filter(
        participants__user=user
    ).select_related('created_by', 'budget_source').prefetch_related('participants', 'documents').distinct()

    stats = _travel_stats_for_queryset(my_travels)
    context = {
        'user':           user,
        'today':          timezone.now().date(),
        'recent_travels': my_travels[:6],
        'doc_types':      TravelDocument.DOC_TYPE_CHOICES,
        **stats,
    }
    return render(request, 'travel_app/employee/dashboard.html', context)


# ══════════════════════════════════════════════════════════════════════
# DEPT SECRETARY DASHBOARD
# ══════════════════════════════════════════════════════════════════════

@never_cache
@require_role(['DEPT_SEC'])
def dept_secretary_dashboard(request, user=None):
    today = timezone.now().date()
    year  = today.year

    college_travels = TravelRecord.objects.filter(
        scope='COLLEGE',
        participants__college_snapshot=user.college.name if user.college else ''
    ).select_related('created_by', 'budget_source').prefetch_related('participants').distinct()

    untagged          = college_travels.filter(budget_source__isnull=True)
    budget_sources    = get_sources_for_secretary(user, year=year)
    total_budget_used = sum(item.get('used', 0) for item in budget_sources)
    duplicate_alerts  = _detect_duplicates(college_travels)

    context = {
        'user':              user,
        'today':             today,
        'current_year':      year,
        'total_travels':     college_travels.count(),
        'untagged_count':    untagged.count(),
        'untagged_travels':  untagged[:8],
        'recent_travels':    college_travels[:8],
        'total_travelers':   sum(t.participant_count for t in college_travels),
        'total_budget_used': total_budget_used,
        'budget_sources':    budget_sources,
        'duplicate_alerts':  duplicate_alerts,
    }
    return render(request, 'travel_app/secretary/dashboard.html', context)


# ══════════════════════════════════════════════════════════════════════
# CAMPUS SECRETARY DASHBOARD
# ══════════════════════════════════════════════════════════════════════

@never_cache
@require_role(['CAMPUS_SEC'])
def campus_secretary_dashboard(request, user=None):
    today = timezone.now().date()
    year  = today.year

    campus_travels = TravelRecord.objects.filter(
        participants__campus_snapshot=user.campus.name if user.campus else ''
    ).select_related('created_by', 'budget_source').prefetch_related('participants').distinct()

    untagged          = campus_travels.filter(budget_source__isnull=True)
    budget_sources    = get_sources_for_secretary(user, year=year)
    total_budget_used = sum(item.get('used', 0) for item in budget_sources)
    duplicate_alerts  = _detect_duplicates(campus_travels)

    context = {
        'user':              user,
        'today':             today,
        'current_year':      year,
        'total_travels':     campus_travels.count(),
        'untagged_count':    untagged.count(),
        'untagged_travels':  untagged[:8],
        'recent_travels':    campus_travels[:8],
        'total_travelers':   sum(t.participant_count for t in campus_travels),
        'total_budget_used': total_budget_used,
        'budget_sources':    budget_sources,
        'duplicate_alerts':  duplicate_alerts,
    }
    return render(request, 'travel_app/secretary/dashboard.html', context)


# ══════════════════════════════════════════════════════════════════════
# ADMIN DASHBOARD
# ══════════════════════════════════════════════════════════════════════

@never_cache
@require_role(['ADMIN'])
def admin_dashboard(request, user=None):
    from accounts.models import College

    today = timezone.now().date()
    year  = today.year

    all_travels = TravelRecord.objects.select_related(
        'created_by__college', 'budget_source'
    ).prefetch_related('participants').all()

    sources     = BudgetSource.objects.filter(year=year, is_active=True)
    budget_data = []
    for source in sources:
        if source.scope == 'COLLEGE':
            usages          = BudgetUsage.objects.filter(budget_source=source, year=year)
            total_allocated = source.college_budget_amount * usages.count() if usages.exists() else source.college_budget_amount
            total_used      = sum(u.used_amount for u in usages)
        else:
            usages          = CampusBudgetUsage.objects.filter(budget_source=source, year=year)
            total_allocated = source.campus_budget_amount
            total_used      = sum(u.used_amount for u in usages)
        pct    = round((total_used / total_allocated * 100), 1) if total_allocated > 0 else 0
        status = 'exhausted' if pct >= 100 else 'critical' if pct >= 80 else 'warning' if pct >= 60 else 'healthy'
        budget_data.append({
            'source':    source,
            'allocated': total_allocated,
            'used':      total_used,
            'remaining': total_allocated - total_used,
            'percentage': pct,
            'status':    status,
        })

    college_stats = []
    for college in College.objects.all():
        count = all_travels.filter(participants__college_snapshot=college.name).distinct().count()
        if count > 0:
            college_stats.append({'college': college.code or college.name[:10], 'count': count})

    context = {
        'user':            user,
        'today':           today,
        'current_year':    year,
        'total_travels':   all_travels.count(),
        'untagged_count':  all_travels.filter(budget_source__isnull=True).count(),
        'total_travelers': sum(t.participant_count for t in all_travels),
        'total_colleges':  College.objects.count(),
        'budget_sources':  budget_data,
        'college_stats':   college_stats,
        'recent_travels':  all_travels[:8],
    }
    return render(request, 'travel_app/admin/dashboard.html', context)


# ══════════════════════════════════════════════════════════════════════
# CREATE TRAVEL
# ══════════════════════════════════════════════════════════════════════

@csrf_protect
@never_cache
def create_travel(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['EMPLOYEE', 'DEPT_SEC', 'CAMPUS_SEC']:
        return redirect('accounts:dashboard')

    today = timezone.now().date()

    if user.role == 'EMPLOYEE':
        available_participants = []
    elif user.role == 'DEPT_SEC':
        available_participants = User.objects.filter(
            college=user.college,
            is_approved=True,
            is_active=True,
            role='EMPLOYEE'
        ).exclude(id=user.id).order_by('last_name', 'first_name')
    else:
        available_participants = User.objects.filter(
            campus=user.campus,
            is_approved=True,
            is_active=True,
            role__in=['EMPLOYEE', 'DEPT_SEC']
        ).exclude(id=user.id).order_by('college__name', 'last_name', 'first_name')

    if request.method == 'POST':
        destination        = request.POST.get('destination', '').strip()
        start_date         = request.POST.get('start_date', '').strip()
        end_date           = request.POST.get('end_date', '').strip() or None
        purpose            = request.POST.get('purpose', '').strip()
        is_out_of_province = request.POST.get('is_out_of_province') == 'on'
        notes              = request.POST.get('notes', '').strip()
        participant_ids    = request.POST.getlist('participants')

        errors = []
        if not destination:
            errors.append('Destination is required.')
        if not start_date:
            errors.append('Start date is required.')
        if not purpose:
            errors.append('Purpose is required.')
        if start_date and end_date and end_date < start_date:
            errors.append('End date cannot be before start date.')
        from datetime import date
        if start_date and start_date < str(date.today()):
            errors.append('Start date cannot be in the past.')

        if errors:
            from django.contrib import messages
            for e in errors:
                messages.error(request, e)
            return render(request, 'travel_app/shared/create_travel.html', {
                'user': user, 'today': today,
                'available_participants': available_participants,
                'post': request.POST,
            })

        try:
            with transaction.atomic():
                travel = TravelRecord.objects.create(
                    destination=destination,
                    start_date=start_date,
                    end_date=end_date,
                    purpose=purpose,
                    is_out_of_province=is_out_of_province,
                    notes=notes,
                    created_by=user,
                    scope='COLLEGE',
                )

                if user.role == 'EMPLOYEE':
                    TravelParticipant.objects.create(travel_record=travel, user=user)
                elif request.POST.get('include_creator') == 'yes':
                    TravelParticipant.objects.create(travel_record=travel, user=user)

                if user.role in ['DEPT_SEC', 'CAMPUS_SEC'] and participant_ids:
                    for pid in participant_ids:
                        try:
                            participant = User.objects.get(id=pid, is_active=True)
                            TravelParticipant.objects.get_or_create(
                                travel_record=travel, user=participant
                            )
                        except User.DoesNotExist:
                            pass

                travel.refresh_scope()
                _notify_if_duplicate(travel, user)

                from django.contrib import messages
                messages.success(request, f'Travel to {destination} created successfully!')
                return redirect('travel_app:travel_detail', pk=travel.id)

        except Exception as e:
            from django.contrib import messages
            messages.error(request, f'Error creating travel: {str(e)}')

    return render(request, 'travel_app/shared/create_travel.html', {
        'user':                   user,
        'today':                  today,
        'available_participants': available_participants,
        'post':                   {},
    })


# ══════════════════════════════════════════════════════════════════════
# TRAVEL DETAIL
# ══════════════════════════════════════════════════════════════════════

@never_cache
def travel_detail(request, pk):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    travel = get_object_or_404(
        TravelRecord.objects.select_related(
            'created_by', 'budget_source', 'event_group',
            'funding_college', 'budget_tagged_by'
        ).prefetch_related('participants__user', 'documents'),
        pk=pk
    )

    is_participant = travel.participants.filter(user=user).exists()
    is_creator     = travel.created_by == user
    is_secretary   = user.role in ['DEPT_SEC', 'CAMPUS_SEC']
    is_admin       = user.role == 'ADMIN'

    if not (is_participant or is_creator or is_secretary or is_admin):
        from django.contrib import messages
        messages.error(request, 'You do not have access to this travel record.')
        return redirect('accounts:dashboard')

    docs_by_type = {}
    for doc_type, doc_label in TravelDocument.DOC_TYPE_CHOICES:
        docs_by_type[doc_type] = {
            'label':     doc_label,
            'documents': travel.documents.filter(doc_type=doc_type).order_by('-uploaded_at'),
            'uploaded':  travel.documents.filter(doc_type=doc_type).exists(),
        }

    can_tag_budget = False
    can_route      = False
    budget_sources = []
    route_colleges = []

    if user.role == 'DEPT_SEC' and user.college:
        if travel.scope == 'COLLEGE':
            travel_colleges = set(
                travel.participants.exclude(college_snapshot='')
                                   .values_list('college_snapshot', flat=True)
            )
            if user.college.name in travel_colleges:
                can_tag_budget = True
                budget_sources = get_sources_for_secretary(user)
        elif travel.scope == 'CAMPUS' and travel.funding_college == user.college:
            can_tag_budget = True
            budget_sources = get_sources_for_secretary(user)

    elif user.role == 'CAMPUS_SEC' and user.campus:
        if travel.scope == 'CAMPUS':
            travel_campuses = set(
                travel.participants.exclude(campus_snapshot='')
                                   .values_list('campus_snapshot', flat=True)
            )
            if user.campus.name in travel_campuses:
                if not travel.funding_college:
                    can_tag_budget = True
                    can_route      = True
                    budget_sources = get_sources_for_secretary(user)
                    from accounts.models import College
                    involved_college_names = set(
                        travel.participants.exclude(college_snapshot='')
                                           .values_list('college_snapshot', flat=True)
                    )
                    route_colleges = College.objects.filter(name__in=involved_college_names)
                else:
                    can_tag_budget = False

    context = {
        'user':           user,
        'travel':         travel,
        'docs_by_type':   docs_by_type,
        'doc_types':      TravelDocument.DOC_TYPE_CHOICES,
        'budget_sources': budget_sources,
        'can_tag_budget': can_tag_budget,
        'can_route':      can_route,
        'route_colleges': route_colleges,
        'is_secretary':   is_secretary,
        'is_admin':       is_admin,
        'is_creator':     is_creator or is_participant,
        'today':          timezone.now().date(),
        'missing_docs':   travel.missing_documents,
    }
    return render(request, 'travel_app/shared/travel_detail.html', context)


# ══════════════════════════════════════════════════════════════════════
# UPLOAD DOCUMENT
# ══════════════════════════════════════════════════════════════════════

# In travel_app/views.py — REPLACE the existing upload_document view with this:

@csrf_protect
@never_cache
def upload_document(request, pk):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    travel = get_object_or_404(TravelRecord, pk=pk)

    is_participant = travel.participants.filter(user=user).exists()
    is_secretary   = user.role in ['DEPT_SEC', 'CAMPUS_SEC']
    is_admin       = user.role == 'ADMIN'

    if not (is_participant or is_secretary or is_admin):
        from django.contrib import messages
        messages.error(request, 'You cannot upload documents to this travel.')
        return redirect('travel_app:travel_detail', pk=pk)

    if request.method == 'POST':
        doc_type = request.POST.get('doc_type')
        file     = request.FILES.get('file')
        notes    = request.POST.get('notes', '').strip()

        if not doc_type or not file:
            from django.contrib import messages
            messages.error(request, 'Document type and file are required.')
            return redirect('travel_app:travel_detail', pk=pk)

        valid_types = [t for t, _ in TravelDocument.DOC_TYPE_CHOICES]
        if doc_type not in valid_types:
            from django.contrib import messages
            messages.error(request, 'Invalid document type.')
            return redirect('travel_app:travel_detail', pk=pk)

        doc = TravelDocument.objects.create(
            travel_record=travel,
            doc_type=doc_type,
            file=file,
            uploaded_by=user,
            notes=notes,
        )

        # ── Trigger AI extraction in background thread ─────────────────
        # Runs asynchronously so upload response is instant
        try:
            from .tasks import extract_document_task
            extract_document_task.delay(doc.id)
        except Exception:
            pass  # Extraction failure should never block upload

        from django.contrib import messages
        messages.success(
            request,
            f'{doc.get_doc_type_display()} uploaded successfully. AI is extracting data in the background.'
        )

    return redirect('travel_app:travel_detail', pk=pk)

# ══════════════════════════════════════════════════════════════════════
# TAG BUDGET
# ══════════════════════════════════════════════════════════════════════

@csrf_protect
@never_cache
def tag_budget(request, pk):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['DEPT_SEC', 'CAMPUS_SEC']:
        from django.contrib import messages
        messages.error(request, 'Only secretaries can tag budget sources.')
        return redirect('travel_app:travel_detail', pk=pk)

    travel = get_object_or_404(TravelRecord, pk=pk)

    if request.method == 'POST':
        action = request.POST.get('action', 'tag')

        # Route to a college (Campus Secretary only)
        if action == 'route' and user.role == 'CAMPUS_SEC':
            from accounts.models import College
            college_id = request.POST.get('funding_college_id')
            try:
                college = College.objects.get(id=college_id)
                travel.funding_college = college
                travel.save(update_fields=['funding_college'])

                dept_secs = User.objects.filter(
                    role='DEPT_SEC', college=college,
                    is_active=True, is_approved=True
                )
                for sec in dept_secs:
                    Notification.objects.create(
                        user=sec,
                        notification_type='BUDGET_TAGGED',
                        title='Travel routed to you for budget tagging',
                        message=(
                            f'Campus Secretary routed a cross-college travel to '
                            f'{travel.destination} ({travel.start_date}) to your queue. '
                            f'Please assign the budget source from your college.'
                        ),
                        travel_record=travel,
                    )

                from django.contrib import messages
                messages.success(request, f'Travel routed to {college.name} Secretary for budget tagging.')
            except College.DoesNotExist:
                from django.contrib import messages
                messages.error(request, 'College not found.')

        # Tag budget directly
        elif action == 'tag':
            budget_source_id = request.POST.get('budget_source_id')
            try:
                source = BudgetSource.objects.get(id=budget_source_id, is_active=True)

                allowed = False
                if user.role == 'DEPT_SEC' and source.scope == 'COLLEGE':
                    allowed = True
                elif user.role == 'CAMPUS_SEC' and source.scope == 'CAMPUS':
                    allowed = True

                if not allowed:
                    from django.contrib import messages
                    messages.error(request, 'You cannot use this budget source.')
                else:
                    from django.utils import timezone as tz
                    from decimal import Decimal

                    travel.budget_source    = source
                    travel.budget_tagged_by = user
                    travel.budget_tagged_at = tz.now()
                    travel.amount_deducted  = Decimal('0')
                    travel.save(update_fields=[
                        'budget_source', 'budget_tagged_by',
                        'budget_tagged_at', 'amount_deducted'
                    ])

                    # Auto-create usage row so it appears in budget overview
                    if source.scope == 'COLLEGE' and user.college:
                        source.get_or_create_college_usage(user.college)
                    elif source.scope == 'CAMPUS' and user.campus:
                        source.get_or_create_campus_usage(user.campus)

                    from django.contrib import messages
                    messages.success(request, f'Budget source "{source.name}" assigned successfully.')

            except BudgetSource.DoesNotExist:
                from django.contrib import messages
                messages.error(request, 'Invalid budget source.')
            except Exception as e:
                from django.contrib import messages
                messages.error(request, f'Error tagging budget: {str(e)}')

    return redirect('travel_app:travel_detail', pk=pk)


# ══════════════════════════════════════════════════════════════════════
# ALL TRAVELS
# ══════════════════════════════════════════════════════════════════════

@never_cache
def all_travels(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    today = timezone.now().date()

    if user.role == 'EMPLOYEE':
        travels = TravelRecord.objects.filter(participants__user=user).distinct()
    elif user.role == 'DEPT_SEC':
        travels = TravelRecord.objects.filter(
            scope='COLLEGE',
            participants__college_snapshot=user.college.name if user.college else ''
        ).distinct()
    elif user.role == 'CAMPUS_SEC':
        travels = TravelRecord.objects.filter(
            participants__campus_snapshot=user.campus.name if user.campus else ''
        ).distinct()
    else:
        travels = TravelRecord.objects.all()

    travels = travels.select_related(
        'created_by__college', 'budget_source'
    ).prefetch_related('participants').order_by('-created_at')

    filter_tagged = request.GET.get('tagged')
    filter_scope  = request.GET.get('scope')
    filter_year   = request.GET.get('year')
    search        = request.GET.get('q', '').strip()

    if filter_tagged == 'yes':
        travels = travels.filter(budget_source__isnull=False)
    elif filter_tagged == 'no':
        travels = travels.filter(budget_source__isnull=True)
    if filter_scope in ['COLLEGE', 'CAMPUS']:
        travels = travels.filter(scope=filter_scope)
    if filter_year:
        travels = travels.filter(start_date__year=filter_year)
    if search:
        travels = travels.filter(destination__icontains=search)

    context = {
        'user':          user,
        'travels':       travels,
        'today':         today,
        'total':         travels.count(),
        'filter_tagged': filter_tagged,
        'filter_scope':  filter_scope,
        'filter_year':   filter_year,
        'search':        search,
        'current_year':  today.year,
    }
    return render(request, 'travel_app/shared/all_travels.html', context)


# ══════════════════════════════════════════════════════════════════════
# MY TRAVELS + STATS
# ══════════════════════════════════════════════════════════════════════

@never_cache
@require_role(['EMPLOYEE'])
def my_travels(request, user=None):
    return redirect('travel_app:all_travels')


@never_cache
@require_role(['EMPLOYEE'])
def my_stats(request, user=None):
    return redirect('travel_app:employee_dashboard')


# ══════════════════════════════════════════════════════════════════════
# MANAGE BUDGET SOURCES (Admin)
# ══════════════════════════════════════════════════════════════════════

@csrf_protect
@never_cache
def manage_budget_sources(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role != 'ADMIN':
        from django.contrib import messages
        messages.error(request, 'Admin access required.')
        return redirect('accounts:dashboard')

    from accounts.models import College, Campus
    today = timezone.now().date()
    year  = int(request.GET.get('year', today.year))

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'create':
            name                  = request.POST.get('name', '').strip()
            scope                 = request.POST.get('scope', 'COLLEGE')
            college_budget_amount = request.POST.get('college_budget_amount', 0) or 0
            campus_budget_amount  = request.POST.get('campus_budget_amount', 0) or 0
            description           = request.POST.get('description', '').strip()
            source_year           = int(request.POST.get('year', today.year))
            if not name:
                from django.contrib import messages
                messages.error(request, 'Budget source name is required.')
            else:
                try:
                    BudgetSource.objects.create(
                        name=name, scope=scope, year=source_year,
                        college_budget_amount=college_budget_amount,
                        campus_budget_amount=campus_budget_amount,
                        description=description,
                    )
                    from django.contrib import messages
                    messages.success(request, f'Budget source "{name}" created.')
                except Exception as e:
                    from django.contrib import messages
                    messages.error(request, f'Error: {str(e)}')

        elif action == 'toggle':
            source_id = request.POST.get('source_id')
            try:
                source = BudgetSource.objects.get(id=source_id)
                source.is_active = not source.is_active
                source.save(update_fields=['is_active'])
                from django.contrib import messages
                messages.success(request, f'"{source.name}" {"activated" if source.is_active else "deactivated"}.')
            except BudgetSource.DoesNotExist:
                from django.contrib import messages
                messages.error(request, 'Budget source not found.')

        elif action == 'delete':
            source_id = request.POST.get('source_id')
            try:
                source = BudgetSource.objects.get(id=source_id)
                if source.travel_records.exists():
                    from django.contrib import messages
                    messages.error(request, f'Cannot delete "{source.name}" — travels are using it.')
                else:
                    name = source.name
                    source.delete()
                    from django.contrib import messages
                    messages.success(request, f'"{name}" deleted.')
            except BudgetSource.DoesNotExist:
                from django.contrib import messages
                messages.error(request, 'Budget source not found.')

        return redirect(f"{request.path}?year={year}")

    sources     = BudgetSource.objects.filter(year=year).order_by('scope', 'name')
    source_data = []
    for source in sources:
        if source.scope == 'COLLEGE':
            usages          = BudgetUsage.objects.filter(budget_source=source, year=year)
            total_allocated = source.college_budget_amount * College.objects.count()
            total_used      = sum(u.used_amount for u in usages)
        else:
            usages          = CampusBudgetUsage.objects.filter(budget_source=source, year=year)
            total_allocated = source.campus_budget_amount
            total_used      = sum(u.used_amount for u in usages)

        pct    = round((total_used / total_allocated * 100), 1) if total_allocated > 0 else 0
        status = 'exhausted' if pct >= 100 else 'critical' if pct >= 80 else 'warning' if pct >= 60 else 'healthy'
        source_data.append({
            'source':       source,
            'allocated':    total_allocated,
            'used':         total_used,
            'remaining':    total_allocated - total_used,
            'percentage':   pct,
            'status':       status,
            'travel_count': source.travel_records.count(),
        })

    context = {
        'user':          user,
        'today':         today,
        'current_year':  year,
        'year_range':    range(today.year - 1, today.year + 3),
        'source_data':   source_data,
        'college_count': College.objects.count(),
    }
    return render(request, 'travel_app/admin/manage_budget_sources.html', context)


# ══════════════════════════════════════════════════════════════════════
# BUDGET OVERVIEW
# ══════════════════════════════════════════════════════════════════════

@never_cache
def budget_overview(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['ADMIN', 'DEPT_SEC', 'CAMPUS_SEC']:
        return redirect('accounts:dashboard')

    from accounts.models import College, Campus
    today = timezone.now().date()
    year  = int(request.GET.get('year', today.year))

    if user.role == 'ADMIN':
        sources  = BudgetSource.objects.filter(year=year, is_active=True).order_by('scope', 'name')
        overview = []
        for source in sources:
            if source.scope == 'COLLEGE':
                usages = BudgetUsage.objects.filter(
                    budget_source=source, year=year
                ).select_related('college').order_by('college__name')
                rows = [{
                    'label':      u.college.name,
                    'allocated':  u.allocated_amount,
                    'used':       u.used_amount,
                    'remaining':  u.remaining_amount,
                    'percentage': u.usage_percentage,
                    'status':     u.status,
                } for u in usages]
                total_alloc  = source.college_budget_amount * College.objects.count()
                total_used   = sum(u.used_amount for u in usages)
                tagged_count = source.travel_records.count()
            else:
                usages = CampusBudgetUsage.objects.filter(
                    budget_source=source, year=year
                ).select_related('campus').order_by('campus__name')
                rows = [{
                    'label':      u.campus.name,
                    'allocated':  u.allocated_amount,
                    'used':       u.used_amount,
                    'remaining':  u.remaining_amount,
                    'percentage': u.usage_percentage,
                    'status':     u.status,
                } for u in usages]
                total_alloc  = source.campus_budget_amount
                total_used   = sum(u.used_amount for u in usages)
                tagged_count = source.travel_records.count()

            pct = round((total_used / total_alloc * 100), 1) if total_alloc > 0 else 0
            overview.append({
                'source':          source,
                'rows':            rows,
                'total_allocated': total_alloc,
                'total_used':      total_used,
                'total_remaining': total_alloc - total_used,
                'percentage':      pct,
                'tagged_count':    tagged_count,
                'status': 'exhausted' if pct >= 100 else 'critical' if pct >= 80 else 'warning' if pct >= 60 else 'healthy',
            })
    else:
        budget_sources = get_sources_for_secretary(user, year=year)
        overview = [{
            'source':          item['source'],
            'rows':            [],
            'total_allocated': item.get('allocated', 0),
            'total_used':      item.get('used', 0),
            'total_remaining': item.get('remaining', 0),
            'percentage':      item.get('percentage', 0),
            'tagged_count':    item['source'].travel_records.count(),
            'status':          item.get('status', 'healthy'),
        } for item in budget_sources]

    context = {
        'user':         user,
        'today':        today,
        'current_year': year,
        'year_range':   range(today.year - 1, today.year + 3),
        'overview':     overview,
    }
    return render(request, 'travel_app/shared/budget_overview.html', context)


# ══════════════════════════════════════════════════════════════════════
# EVENT GROUPS
# ══════════════════════════════════════════════════════════════════════

@never_cache
def event_groups(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['ADMIN', 'DEPT_SEC', 'CAMPUS_SEC']:
        return redirect('accounts:dashboard')

    today  = timezone.now().date()
    groups = EventGroup.objects.select_related('created_by').prefetch_related(
        'travel_records__participants'
    ).order_by('-start_date')

    if user.role == 'DEPT_SEC' and user.college:
        groups = groups.filter(
            travel_records__participants__college_snapshot=user.college.name
        ).distinct()
    elif user.role == 'CAMPUS_SEC' and user.campus:
        groups = groups.filter(
            travel_records__participants__campus_snapshot=user.campus.name
        ).distinct()

    if user.role == 'DEPT_SEC':
        ungrouped = TravelRecord.objects.filter(
            scope='COLLEGE', event_group__isnull=True,
            participants__college_snapshot=user.college.name if user.college else ''
        ).distinct()
    elif user.role == 'CAMPUS_SEC':
        ungrouped = TravelRecord.objects.filter(
            event_group__isnull=True,
            participants__campus_snapshot=user.campus.name if user.campus else ''
        ).distinct()
    else:
        ungrouped = TravelRecord.objects.filter(event_group__isnull=True)

    context = {
        'user':             user,
        'today':            today,
        'groups':           groups,
        'duplicate_alerts': _detect_duplicates(ungrouped),
    }
    return render(request, 'travel_app/shared/event_groups.html', context)


# ══════════════════════════════════════════════════════════════════════
# SECRETARY QUEUE
# ══════════════════════════════════════════════════════════════════════

@never_cache
def secretary_queue(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['DEPT_SEC', 'CAMPUS_SEC']:
        return redirect('accounts:dashboard')

    if user.role == 'DEPT_SEC' and user.college:
        own_college = TravelRecord.objects.filter(
            scope='COLLEGE',
            budget_source__isnull=True,
            participants__college_snapshot=user.college.name
        ).distinct()
        routed = TravelRecord.objects.filter(
            scope='CAMPUS',
            budget_source__isnull=True,
            funding_college=user.college
        ).distinct()
        queue = list(own_college) + list(routed)

    elif user.role == 'CAMPUS_SEC' and user.campus:
        queue = TravelRecord.objects.filter(
            scope='CAMPUS',
            budget_source__isnull=True,
            funding_college__isnull=True,
            participants__campus_snapshot=user.campus.name
        ).distinct()
    else:
        queue = []

    context = {
        'user':  user,
        'queue': queue,
        'today': timezone.now().date(),
    }
    return render(request, 'travel_app/secretary/queue.html', context)

@never_cache
def download_zip(request, pk):
    import zipfile
    import io
    import os
    from django.http import HttpResponse

    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    travel = get_object_or_404(TravelRecord, pk=pk)

    is_participant = travel.participants.filter(user=user).exists()
    is_secretary   = user.role in ['DEPT_SEC', 'CAMPUS_SEC']
    is_admin       = user.role == 'ADMIN'

    if not (is_participant or is_secretary or is_admin):
        from django.contrib import messages
        messages.error(request, 'You do not have access to this travel.')
        return redirect('accounts:dashboard')

    documents = travel.documents.all()
    if not documents.exists():
        from django.contrib import messages
        messages.error(request, 'No documents to download.')
        return redirect('travel_app:travel_detail', pk=pk)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for doc in documents:
            try:
                file_path = doc.file.path
                file_name = f"{doc.get_doc_type_display()} - {os.path.basename(file_path)}"
                zf.write(file_path, arcname=file_name)
            except Exception:
                pass

    buffer.seek(0)
    zip_name = f"Travel_{travel.destination}_{travel.start_date}.zip".replace(' ', '_')
    response = HttpResponse(buffer, content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{zip_name}"'
    return response

# ADD THESE TWO VIEWS to travel_app/views.py

@csrf_protect
@never_cache
def confirm_extraction(request, doc_id):
    """
    Secretary confirms the AI-extracted data is correct.
    This applies the extracted amount to the budget deduction.
    """
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['DEPT_SEC', 'CAMPUS_SEC', 'ADMIN']:
        from django.contrib import messages
        messages.error(request, 'Access denied.')
        return redirect('accounts:dashboard')

    from .models import TravelDocument
    from django.utils import timezone as tz
    from decimal import Decimal

    doc = get_object_or_404(TravelDocument, id=doc_id)
    travel = doc.travel_record

    if request.method == 'POST':
        # Mark document as confirmed
        doc.is_confirmed  = True
        doc.confirmed_by  = user
        doc.confirmed_at  = tz.now()
        doc.save(update_fields=['is_confirmed', 'confirmed_by', 'confirmed_at'])

        # If this doc has an extracted amount and travel has a budget source,
        # update the travel's amount_deducted and adjust the usage record
        if doc.extracted_amount and travel.budget_source:
            old_amount = travel.amount_deducted or Decimal('0')
            new_amount = doc.extracted_amount

            # Only update if new amount is different
            if new_amount != old_amount:
                source = travel.budget_source

                # Get usage record
                if source.scope == 'COLLEGE' and user.college:
                    try:
                        usage = BudgetUsage.objects.get(
                            college=user.college,
                            budget_source=source,
                            year=source.year
                        )
                        # Restore old amount then deduct new amount
                        usage.restore(old_amount)
                        usage.deduct(new_amount)
                    except BudgetUsage.DoesNotExist:
                        pass
                elif source.scope == 'CAMPUS' and user.campus:
                    try:
                        usage = CampusBudgetUsage.objects.get(
                            campus=user.campus,
                            budget_source=source,
                            year=source.year
                        )
                        usage.restore(old_amount)
                        usage.deduct(new_amount)
                    except CampusBudgetUsage.DoesNotExist:
                        pass

                # Update travel's deducted amount
                travel.amount_deducted = new_amount
                travel.save(update_fields=['amount_deducted'])

        from django.contrib import messages
        messages.success(request, 'Extraction confirmed and budget updated.')

    return redirect('travel_app:travel_detail', pk=travel.id)


@csrf_protect
@never_cache
def reject_extraction(request, doc_id):
    """
    Secretary rejects the AI extraction — marks it as not confirmed.
    Document stays but extracted data is cleared.
    """
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['DEPT_SEC', 'CAMPUS_SEC', 'ADMIN']:
        from django.contrib import messages
        messages.error(request, 'Access denied.')
        return redirect('accounts:dashboard')

    from .models import TravelDocument

    doc = get_object_or_404(TravelDocument, id=doc_id)
    travel = doc.travel_record

    if request.method == 'POST':
        # Clear extracted data
        doc.extracted_destination   = ''
        doc.extracted_start_date    = None
        doc.extracted_end_date      = None
        doc.extracted_amount        = None
        doc.extracted_purpose       = ''
        doc.extracted_num_travelers = None
        doc.extraction_successful   = False
        doc.extraction_attempted    = False  # Allow re-extraction after re-upload
        doc.save(update_fields=[
            'extracted_destination', 'extracted_start_date', 'extracted_end_date',
            'extracted_amount', 'extracted_purpose', 'extracted_num_travelers',
            'extraction_successful', 'extraction_attempted'
        ])

        from django.contrib import messages
        messages.warning(request, 'Extraction rejected. Please re-upload the correct document.')

    return redirect('travel_app:travel_detail', pk=travel.id)

    # ══════════════════════════════════════════════════════════════════════
# STATS VIEW
# ══════════════════════════════════════════════════════════════════════

from django.db.models import Count, Sum, Avg, Q
from django.db.models.functions import TruncMonth, TruncYear
from datetime import date, timedelta
import json as json_module

def stats_view(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['ADMIN', 'CAMPUS_SEC', 'DEPT_SEC']:
        return redirect('travel_app:employee_dashboard')

    today      = date.today()
    this_year  = today.year
    this_month = today.month

    # ── Scope filter ─────────────────────────────────────────────────
    # Base queryset scoped per role
    if user.role == 'ADMIN':
        travels = TravelRecord.objects.all()
    elif user.role == 'CAMPUS_SEC':
        travels = TravelRecord.objects.filter(
            participants__campus_snapshot=user.campus.name
        ).distinct()
    else:  # DEPT_SEC
        travels = TravelRecord.objects.filter(
            participants__college_snapshot=user.college.name
        ).distinct()

    # ── Year filter from GET param (default current year) ────────────
    selected_year = int(request.GET.get('year', this_year))
    travels_year  = travels.filter(start_date__year=selected_year)

    # Available years for dropdown
    all_years = (
        travels
        .dates('start_date', 'year')
        .values_list('start_date__year', flat=True)
    )
    # Use a simpler approach for year list
    year_list = sorted(set(
        TravelRecord.objects.filter(
            id__in=travels.values_list('id', flat=True)
        ).dates('start_date', 'year').values_list('start_date__year', flat=True)
    ), reverse=True)
    if not year_list:
        year_list = [this_year]

    # ══════════════════════════════════════════════════════════════════
    # SECTION 1 — TRAVEL VOLUME
    # ══════════════════════════════════════════════════════════════════

    # Travels per month for selected year (Jan–Dec)
    monthly_raw = (
        travels_year
        .annotate(month=TruncMonth('start_date'))
        .values('month')
        .annotate(count=Count('id'))
        .order_by('month')
    )
    monthly_counts = {i: 0 for i in range(1, 13)}
    for row in monthly_raw:
        monthly_counts[row['month'].month] = row['count']
    monthly_labels = ['Jan','Feb','Mar','Apr','May','Jun',
                      'Jul','Aug','Sep','Oct','Nov','Dec']
    monthly_data   = [monthly_counts[i] for i in range(1, 13)]

    # Travels by scope
    scope_data = {
        'COLLEGE': travels_year.filter(scope='COLLEGE').count(),
        'CAMPUS':  travels_year.filter(scope='CAMPUS').count(),
    }

    # Travels per college (top 8)
    college_volume = (
        travels_year
        .values('participants__college_snapshot')
        .annotate(count=Count('id', distinct=True))
        .exclude(participants__college_snapshot='')
        .order_by('-count')[:8]
    )
    college_vol_labels = [r['participants__college_snapshot'] or 'Unknown' for r in college_volume]
    college_vol_data   = [r['count'] for r in college_volume]

    # Yearly totals (all years)
    yearly_raw = (
        travels
        .annotate(yr=TruncYear('start_date'))
        .values('yr')
        .annotate(count=Count('id'))
        .order_by('yr')
    )
    yearly_labels = [str(r['yr'].year) for r in yearly_raw]
    yearly_data   = [r['count'] for r in yearly_raw]

    # ══════════════════════════════════════════════════════════════════
    # SECTION 2 — BUDGET USAGE
    # ══════════════════════════════════════════════════════════════════

    if user.role == 'ADMIN':
        budget_usages   = BudgetUsage.objects.filter(year=selected_year).select_related('budget_source', 'college')
        campus_usages   = CampusBudgetUsage.objects.filter(year=selected_year).select_related('budget_source', 'campus')
    elif user.role == 'CAMPUS_SEC':
        budget_usages   = BudgetUsage.objects.none()
        campus_usages   = CampusBudgetUsage.objects.filter(year=selected_year, campus=user.campus).select_related('budget_source')
    else:  # DEPT_SEC
        budget_usages   = BudgetUsage.objects.filter(year=selected_year, college=user.college).select_related('budget_source')
        campus_usages   = CampusBudgetUsage.objects.none()

    # Monthly spend (amount_deducted per month for selected year)
    monthly_spend_raw = (
        travels_year
        .filter(budget_source__isnull=False)
        .annotate(month=TruncMonth('start_date'))
        .values('month')
        .annotate(total=Sum('amount_deducted'))
        .order_by('month')
    )
    monthly_spend = {i: 0 for i in range(1, 13)}
    for row in monthly_spend_raw:
        monthly_spend[row['month'].month] = float(row['total'] or 0)
    monthly_spend_data = [monthly_spend[i] for i in range(1, 13)]

    # Total budget stats
    total_allocated = sum(u.allocated_amount for u in budget_usages) + \
                      sum(u.allocated_amount for u in campus_usages)
    total_used      = sum(u.used_amount for u in budget_usages) + \
                      sum(u.used_amount for u in campus_usages)
    total_remaining = total_allocated - total_used

    # ══════════════════════════════════════════════════════════════════
    # SECTION 3 — PARTICIPANT STATS
    # ══════════════════════════════════════════════════════════════════

    from .models import TravelParticipant

    # Top 8 most frequent travelers
    top_travelers = (
        TravelParticipant.objects
        .filter(travel_record__in=travels_year)
        .values('user__first_name', 'user__last_name', 'college_snapshot')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )

    # Average participants per travel
    avg_participants = travels_year.annotate(
        pcount=Count('participants')
    ).aggregate(avg=Avg('pcount'))['avg'] or 0

    # Total participant-days
    total_participant_days = 0
    for t in travels_year.annotate(pcount=Count('participants')):
        total_participant_days += t.pcount * t.get_duration_days()

    # Travels per college (participant perspective)
    college_participation = (
        TravelParticipant.objects
        .filter(travel_record__in=travels_year)
        .values('college_snapshot')
        .annotate(count=Count('id'))
        .exclude(college_snapshot='')
        .order_by('-count')[:8]
    )

    # ══════════════════════════════════════════════════════════════════
    # SECTION 4 — ANOMALY ALERTS
    # ══════════════════════════════════════════════════════════════════

    anomalies = []

    # 1. Travels with zero documents
    no_docs = travels_year.annotate(doc_count=Count('documents')).filter(doc_count=0)
    if no_docs.exists():
        anomalies.append({
            'type':     'warning',
            'icon':     'bi-folder-x',
            'title':    f'{no_docs.count()} travel(s) with no documents uploaded',
            'detail':   ', '.join([str(t.destination) for t in no_docs[:3]]) +
                        ('...' if no_docs.count() > 3 else ''),
            'travels':  list(no_docs.values('id', 'destination', 'start_date')[:5]),
        })

    # 2. Budget sources at ≥80% usage
    critical_budgets = [u for u in list(budget_usages) + list(campus_usages)
                        if u.usage_percentage >= 80]
    for u in critical_budgets:
        label = u.college.name if hasattr(u, 'college') else u.campus.name
        anomalies.append({
            'type':   'critical' if u.usage_percentage >= 100 else 'warning',
            'icon':   'bi-exclamation-triangle-fill',
            'title':  f'{u.budget_source.name} — {label} at {u.usage_percentage}%',
            'detail': f'₱{u.used_amount:,.0f} used of ₱{u.allocated_amount:,.0f}',
            'travels': [],
        })

    # 3. Travels with no budget tagged
    untagged = travels_year.filter(budget_source__isnull=True)
    if untagged.exists():
        anomalies.append({
            'type':    'info',
            'icon':    'bi-tag',
            'title':   f'{untagged.count()} travel(s) with no budget tagged',
            'detail':  ', '.join([str(t.destination) for t in untagged[:3]]) +
                       ('...' if untagged.count() > 3 else ''),
            'travels': list(untagged.values('id', 'destination', 'start_date')[:5]),
        })

    # 4. Possible duplicate travels (same destination, dates within 3 days)
    duplicates = []
    travel_list = list(travels_year.values('id', 'destination', 'start_date'))
    seen = []
    for t in travel_list:
        for s in seen:
            if (t['destination'].lower() == s['destination'].lower() and
                    abs((t['start_date'] - s['start_date']).days) <= 3 and
                    t['id'] != s['id']):
                pair = tuple(sorted([t['id'], s['id']]))
                if pair not in duplicates:
                    duplicates.append(pair)
        seen.append(t)

    if duplicates:
        anomalies.append({
            'type':    'info',
            'icon':    'bi-copy',
            'title':   f'{len(duplicates)} possible duplicate travel(s) detected',
            'detail':  'Same destination within 3 days of each other',
            'travels': [],
        })

    # ── Summary cards ─────────────────────────────────────────────────
    total_travels    = travels_year.count()
    total_this_month = travels_year.filter(start_date__month=this_month).count()
    out_of_province  = travels_year.filter(is_out_of_province=True).count()

    context = {
        'user': user,
        'is_admin':      user.role == 'ADMIN',
        'is_secretary':  user.role in ['CAMPUS_SEC', 'DEPT_SEC'],

        # Year filter
        'selected_year': selected_year,
        'year_list':     year_list,
        'this_year':     this_year,

        # Summary
        'total_travels':       total_travels,
        'total_this_month':    total_this_month,
        'out_of_province':     out_of_province,
        'avg_participants':    round(avg_participants, 1),
        'total_participant_days': total_participant_days,
        'total_allocated':     total_allocated,
        'total_used':          total_used,
        'total_remaining':     total_remaining,

        # Charts — serialized as JSON for Chart.js
        'monthly_labels':      json_module.dumps(monthly_labels),
        'monthly_data':        json_module.dumps(monthly_data),
        'yearly_labels':       json_module.dumps(yearly_labels),
        'yearly_data':         json_module.dumps(yearly_data),
        'scope_data':          json_module.dumps(scope_data),
        'college_vol_labels':  json_module.dumps(college_vol_labels),
        'college_vol_data':    json_module.dumps(college_vol_data),
        'monthly_spend_data':  json_module.dumps(monthly_spend_data),
        'monthly_labels_spend':json_module.dumps(monthly_labels),

        # Tables
        'budget_usages':       budget_usages,
        'campus_usages':       campus_usages,
        'top_travelers':       top_travelers,
        'college_participation': college_participation,

        # Anomalies
        'anomalies':           anomalies,
        'anomaly_count':       len(anomalies),
    }

    return render(request, 'travel_app/shared/stats.html', context)