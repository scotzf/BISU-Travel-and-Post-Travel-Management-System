from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.utils import timezone
from django.http import JsonResponse
from django.db import transaction
from accounts.views import get_authenticated_user
from accounts.models import User
import os
import logging
logger = logging.getLogger(__name__)
from .models import (
    TravelRecord, ParticipantDocument, TravelParticipant,
    BudgetSource, BudgetUsage, Notification
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
    ).select_related('created_by', 'budget_source').prefetch_related('participants').distinct()

    stats = _travel_stats_for_queryset(my_travels)
    context = {
        'user':           user,
        'today':          timezone.now().date(),
        'recent_travels': my_travels[:6],
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
        participants__college_name=user.college.name if user.college else ''
    ).select_related('created_by', 'budget_source').prefetch_related('participants').distinct()

    untagged          = college_travels.filter(budget_source__isnull=True)
    budget_sources    = get_sources_for_secretary(user, year=year)
    total_budget_used = sum(item.get('used', 0) for item in budget_sources)
    from django.db.models import Count
    from django.db.models.functions import TruncMonth
    
    travels_year = college_travels.filter(start_date__year=year)
    total_travelers_year = TravelParticipant.objects.filter(
        travel_record__in=travels_year
    ).values('user').distinct().count()
    out_of_province = travels_year.filter(is_out_of_province=True).count()
    
    # Scope data
    scope_college = travels_year.filter(scope='COLLEGE').count()
    scope_campus  = travels_year.filter(scope='CAMPUS').count()
    in_province   = travels_year.filter(is_out_of_province=False).count()
    out_of_province_count = travels_year.filter(is_out_of_province=True).count()
    
    # Monthly data
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
    monthly_labels = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    monthly_data   = [monthly_counts[i] for i in range(1, 13)]
    
    # Top destinations
    top_destinations = (
        travels_year
        .values('destination')
        .annotate(count=Count('id'))
        .order_by('-count')[:5]
    )
    # Normalize budget sources for template
    for src in budget_sources:
        allocated = float(src.get('allocated', 0))
        used = float(src.get('used', 0))
        src['percent'] = src.get('percentage', 0)
        src['total'] = allocated
        src['name'] = src['source'].budget_name

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
        'total_travels_year':    travels_year.count(),
        'total_travelers_year':  total_travelers_year,
        'out_of_province':       out_of_province,
        'scope_college':         scope_college,
        'scope_campus':          scope_campus,
        'monthly_labels':        monthly_labels,
        'monthly_data':          monthly_data,
        'top_destinations':      top_destinations,
        'college_breakdown':      [
            {'name': 'In-Province', 'count': in_province},
            {'name': 'Out-of-Province', 'count': out_of_province_count},
        ],
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
        participants__campus_name=user.campus.name if user.campus else ''
    ).select_related('created_by', 'budget_source').prefetch_related('participants').distinct()

    untagged          = campus_travels.filter(budget_source__isnull=True)
    budget_sources    = get_sources_for_secretary(user, year=year)
    total_budget_used = sum(item.get('used', 0) for item in budget_sources)
    from django.db.models import Count
    from django.db.models.functions import TruncMonth
    
    travels_year = campus_travels.filter(start_date__year=year)
    total_travelers_year = TravelParticipant.objects.filter(
        travel_record__in=travels_year
    ).values('user').distinct().count()
    out_of_province = travels_year.filter(is_out_of_province=True).count()
    
    # Scope data
    scope_college = travels_year.filter(scope='COLLEGE').count()
    scope_campus  = travels_year.filter(scope='CAMPUS').count()
    in_province   = travels_year.filter(is_out_of_province=False).count()
    out_of_province_count = travels_year.filter(is_out_of_province=True).count()

   

    from accounts.models import College
    college_breakdown = []
    for college in College.objects.all().order_by('name'):
        count = travels_year.filter(participants__college_name=college.name).distinct().count()
        if count > 0:
            college_breakdown.append({'name': college.name, 'count': count})
    
    # Monthly data
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
    monthly_labels = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    monthly_data   = [monthly_counts[i] for i in range(1, 13)]
    
    # Top destinations
    top_destinations = (
        travels_year
        .values('destination')
        .annotate(count=Count('id'))
        .order_by('-count')[:5]
    )
    # Normalize budget sources for template
    for src in budget_sources:
        allocated = float(src.get('allocated', 0))
        used = float(src.get('used', 0))
        src['percent'] = src.get('percentage', 0)
        src['total'] = allocated
        src['name'] = src['source'].budget_name

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
        'total_travels_year':    travels_year.count(),
        'total_travelers_year':  total_travelers_year,
        'out_of_province':       out_of_province,
        'scope_college':         scope_college,
        'scope_campus':          scope_campus,
        'monthly_labels':        monthly_labels,
        'monthly_data':          monthly_data,
        'top_destinations':      top_destinations,
        'college_breakdown': college_breakdown,
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

    sources     = BudgetSource.objects.filter(fiscal_year=year, is_active=True)
    budget_data = []
    for source in sources:
        usages          = BudgetUsage.objects.filter(budget_source=source, year=year)
        total_allocated = source.budget_amount * usages.count() if usages.exists() else source.budget_amount
        total_used      = sum(u.used_amount for u in usages)
        pct    = round((total_used / total_allocated * 100), 1) if total_allocated > 0 else 0
        status = 'exhausted' if pct >= 100 else 'critical' if pct >= 80 else 'warning' if pct >= 60 else 'healthy'
        budget_data.append({
            'source':     source,
            'allocated':  total_allocated,
            'used':       total_used,
            'remaining':  total_allocated - total_used,
            'percentage': pct,
            'status':     status,
        })

    college_stats = []
    for college in College.objects.all():
        count = all_travels.filter(participants__college_name=college.name).distinct().count()
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
    else:  # CAMPUS_SEC
        available_participants = User.objects.filter(
            campus=user.campus,
            is_approved=True,
            is_active=True,
            role__in=['EMPLOYEE', 'DEPT_SEC']
        ).exclude(id=user.id).order_by('college__name', 'last_name', 'first_name')

    if request.method == 'POST':
        destination          = request.POST.get('destination', '').strip()
        start_date           = request.POST.get('start_date', '').strip()
        end_date             = request.POST.get('end_date', '').strip() or None
        purpose              = request.POST.get('purpose', '').strip()
        is_out_of_province   = request.POST.get('is_out_of_province') == 'on'
        notes                = request.POST.get('notes', '').strip()
        participant_ids      = request.POST.getlist('participants')
        matched_traveler_ids = request.POST.getlist('matched_travelers')

        errors = []
        if not destination:
            errors.append('Destination is required.')
        if not start_date:
            errors.append('Start date is required.')
        if not purpose:
            errors.append('Purpose is required.')
        if start_date and end_date and end_date < start_date:
            errors.append('End date cannot be before start date.')

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

                scope_override = request.POST.get('scope_override', '').strip()
                if scope_override in ['COLLEGE', 'CAMPUS']:
                    travel.scope = scope_override
                    travel.save(update_fields=['scope'])

                if user.role == 'EMPLOYEE':
                    # Employee is always a participant in their own travel
                    TravelParticipant.objects.get_or_create(travel_record=travel, user=user)
                elif request.POST.get('include_creator') == 'yes':
                    # Secretary opted in via checkbox
                    TravelParticipant.objects.get_or_create(travel_record=travel, user=user)

                for pid in matched_traveler_ids:
                    try:
                        participant = User.objects.get(id=pid, is_active=True)
                        TravelParticipant.objects.get_or_create(travel_record=travel, user=participant)
                    except User.DoesNotExist:
                        pass

                if user.role in ['DEPT_SEC', 'CAMPUS_SEC'] and participant_ids:
                    for pid in participant_ids:
                        try:
                            participant = User.objects.get(id=pid, is_active=True)
                            TravelParticipant.objects.get_or_create(travel_record=travel, user=participant)
                        except User.DoesNotExist:
                            pass

                if not scope_override:
                    travel.refresh_scope()
                unregistered_names = request.POST.getlist('unregistered_travelers')
                for name in unregistered_names:
                    name = name.strip()
                    if name:
                        TravelParticipant.objects.create(
                            travel_record=travel,
                            user=None,
                            name=name,
                            is_registered=False,
                        )
                temp_path = request.session.pop('temp_travel_order_path', None)
                request.session.pop('temp_travel_order_names', [])

                if temp_path:
                    try:
                        from django.core.files.storage import default_storage
                        from django.core.files import File
                        from datetime import datetime
                        from .tasks import extract_document_task

                        full_path = default_storage.path(temp_path)
                        file_name = os.path.basename(temp_path)

                        # Collect doc IDs to run extraction after transaction
                        doc_ids = []

                        for participant in travel.participants.all():
                            with open(full_path, 'rb') as f:
                                doc = ParticipantDocument(
                                    participant=participant,
                                    doc_type='TRAVEL_ORDER',
                                    uploaded_by=user,
                                    extracted_destination=destination,
                                    extracted_purpose=purpose,
                                    extraction_attempted=True,
                                    extraction_successful=False,
                                )
                                if start_date:
                                    doc.extracted_start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
                                if end_date:
                                    doc.extracted_end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
                                doc.file.save(file_name, File(f), save=True)
                                doc_ids.append(doc.id)

                        default_storage.delete(temp_path)

                        # Queue Celery tasks AFTER transaction completes
                        # so the docs exist in DB when the task runs
                        for doc_id in doc_ids:
                            extract_document_task.delay(doc_id)

                    except Exception as e:
                        logger.error(f"Failed to copy travel order to participants: {e}")

                _notify_if_duplicate(travel, user)

                from django.contrib import messages
                messages.success(request, f'Travel to {destination} created successfully!')
                return redirect('travel_app:travel_detail', pk=travel.id)

        except Exception as e:
            import traceback; print(traceback.format_exc()); messages.error(request, f"Error creating travel: {str(e)}")
            import traceback
            messages.error(request, traceback.format_exc())
            import traceback; print(traceback.format_exc()); messages.error(request, f"Error creating travel: {str(e)}")

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
            'created_by', 'budget_source',
            'funding_college', 'budget_tagged_by'
        ).prefetch_related('participants__user', 'participants__documents'),
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
 
    all_participants = travel.participants.select_related('user').all()
 
    if user.role in ['DEPT_SEC', 'CAMPUS_SEC', 'ADMIN']:
        if user.role == 'DEPT_SEC' and user.college:
            if travel.scope == 'CAMPUS' and travel.funding_college == user.college:
                hub_participants = all_participants
            else:
                hub_participants = all_participants.filter(college_name=user.college.name)
        else:
            hub_participants = all_participants
        participant_hubs = []
        for p in hub_participants:
            docs_by_type = {}
            for doc_type, doc_label in ParticipantDocument.DOC_TYPE_CHOICES:
                docs = p.documents.filter(doc_type=doc_type).order_by('-uploaded_at')
                docs_by_type[doc_type] = {
                    'label':     doc_label,
                    'documents': docs,
                    'uploaded':  docs.exists(),
                }
            participant_hubs.append({
                'participant':  p,
                'docs_by_type': docs_by_type,
            })
    else:
        participant_hubs = []
        my_participant = travel.participants.filter(user=user).first()
        if my_participant:
            docs_by_type = {}
            for doc_type, doc_label in ParticipantDocument.DOC_TYPE_CHOICES:
                docs = my_participant.documents.filter(doc_type=doc_type).order_by('-uploaded_at')
                docs_by_type[doc_type] = {
                    'label':     doc_label,
                    'documents': docs,
                    'uploaded':  docs.exists(),
                }
            participant_hubs.append({
                'participant':  my_participant,
                'docs_by_type': docs_by_type,
            })
 
    can_tag_budget = False
    can_route      = False
    budget_sources = []
    route_colleges = []
 
    if user.role == 'DEPT_SEC' and user.college:
        if travel.scope == 'COLLEGE':
            travel_colleges = set(
                travel.participants.exclude(college_name='')
                                   .values_list('college_name', flat=True)
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
                travel.participants.exclude(campus_name='')
                                   .values_list('campus_name', flat=True)
            )
            if user.campus.name in travel_campuses:
                if not travel.funding_college:
                    can_tag_budget = True
                    can_route      = True
                    budget_sources = get_sources_for_secretary(user)
                    from accounts.models import College
                    involved_college_names = set(
                        travel.participants.exclude(college_name='')
                                           .values_list('college_name', flat=True)
                    )
                    route_colleges = College.objects.filter(name__in=involved_college_names)
                else:
                    can_tag_budget = False
 
    # ── Per-participant expense summary — filtered by role ──────────────
    from decimal import Decimal
 
    if user.role == 'DEPT_SEC' and user.college:
        if travel.scope == 'CAMPUS' and travel.funding_college == user.college:
            expense_participants = travel.participants.select_related('user').all()
        else:
            expense_participants = travel.participants.select_related('user').filter(
                college_name=user.college.name
            )
    elif user.role == 'EMPLOYEE':
        my_p = travel.participants.filter(user=user).first()
        expense_participants = travel.participants.filter(id=my_p.id) if my_p else travel.participants.none()
    else:
        expense_participants = travel.participants.select_related('user').all()
 
    participant_expense_summary = []
    total_submitted = Decimal('0')
    all_submitted   = True
 
    for p in expense_participants:
        amount_doc = ParticipantDocument.objects.filter(
            participant=p,
            doc_type='ACTUAL_ITINERARY',
            extracted_amount__isnull=False,
            is_confirmed=True
        ).order_by('-uploaded_at').first()
        if not amount_doc:
            amount_doc = ParticipantDocument.objects.filter(
                participant=p,
                doc_type__in=['BURS', 'ITINERARY'],
                extracted_amount__isnull=False
            ).order_by('-uploaded_at').first()
 
        submitted_amount = amount_doc.extracted_amount if amount_doc else None
 
        if submitted_amount is not None:
            total_submitted += submitted_amount
        else:
            all_submitted = False
 
        participant_expense_summary.append({
            'participant': p,
            'amount':      submitted_amount,
            'has_amount':  submitted_amount is not None,
            'doc_type':    amount_doc.get_doc_type_display() if amount_doc else None,
        })
 
    # ── Unregistered travelers ──────────────────────────────────────────
    from .models import TravelInvite
 
    unregistered_travelers = []
    active_invites = []
    if is_secretary:
        active_invite_objs = TravelInvite.objects.filter(
            travel=travel, is_used=False
        )
        active_invite_names = set(o.invited_name for o in active_invite_objs)

        # Build invite list with URLs for the modal
        for inv in active_invite_objs:
            active_invites.append({
                'name': inv.invited_name,
                'url':  request.build_absolute_uri(f'/invite/{inv.token}/'),
                'expires_at': inv.expires_at,
            })

        # Only show unregistered participants who haven't been invited yet
        registered_names = set(
            p.user.get_full_name().lower()
            for p in all_participants if p.is_registered and p.user
        )
        unregistered_travelers = [
            p.name for p in all_participants
            if not p.is_registered
            and p.name not in active_invite_names
            and p.name.lower() not in registered_names
        ]
 
    # ── Completeness percentage — filtered by role ──────────────────────
    doc_types_count = len(ParticipantDocument.DOC_TYPE_CHOICES)
 
    if user.role == 'EMPLOYEE':
        my_participant = travel.participants.filter(user=user).first()
        if my_participant:
            uploaded       = my_participant.documents.count()
            total_possible = doc_types_count - (0 if travel.is_out_of_province else 1)
            completeness_percentage = round((uploaded / total_possible) * 100) if total_possible else 0
        else:
            completeness_percentage = 0
 
    elif user.role in ['DEPT_SEC', 'CAMPUS_SEC']:
        if user.role == 'DEPT_SEC' and user.college:
            relevant_participants = travel.participants.filter(college_name=user.college.name)
        else:
            relevant_participants = travel.participants.filter(
                campus_name=user.campus.name if user.campus else ''
            )
        count = relevant_participants.count()
        if count:
            total_possible = count * doc_types_count
            if not travel.is_out_of_province:
                total_possible -= count
            uploaded = ParticipantDocument.objects.filter(
                participant__in=relevant_participants
            ).count()
            completeness_percentage = round((uploaded / total_possible) * 100) if total_possible else 0
        else:
            completeness_percentage = 0
 
    else:
        completeness_percentage = travel.completeness_percentage
 
    # ── Liquidation summary ─────────────────────────────────────────────
    liquidation_summary = []
    for p in expense_participants:
        planned_doc = p.documents.filter(
            doc_type='ITINERARY',
            extracted_amount__isnull=False
        ).order_by('-uploaded_at').first()
        actual_doc = p.documents.filter(
            doc_type='ACTUAL_ITINERARY',
            extracted_amount__isnull=False
        ).order_by('-uploaded_at').first()
        if actual_doc:
            planned = planned_doc.extracted_amount if planned_doc else None
            actual  = actual_doc.extracted_amount if actual_doc else None
            diff    = (actual - planned) if (planned and actual) else None
            liquidation_summary.append({
                'name':           p.user.get_full_name() if p.user else p.name,
                'planned':        planned,
                'actual':         actual,
                'difference':     diff,
                'difference_abs': abs(diff) if diff else None,
            })
 
    # ── Missing itinerary participants (for budget tag block) ───────────
    missing_itinerary_participants = []
    if is_secretary and not travel.is_budget_tagged:
        for tp in travel.participants.filter(is_registered=True).select_related('user'):
            has_amount = ParticipantDocument.objects.filter(
                participant=tp,
                doc_type='ITINERARY',
                extracted_amount__isnull=False
            ).exists()
            if not has_amount:
                missing_itinerary_participants.append(tp.get_display_name())
    # ── Unregistered participants (blocks budget tagging) ──
    unregistered_for_tag = [
        p.name for p in travel.participants.filter(is_registered=False)
    ]
    
    context = {
        'user':             user,
        'travel':           travel,
        'participant_hubs': participant_hubs,
        'doc_types':        ParticipantDocument.DOC_TYPE_CHOICES,
        'budget_sources':   budget_sources,
        'can_tag_budget':   can_tag_budget,
        'can_route':        can_route,
        'route_colleges':   route_colleges,
        'is_secretary':     is_secretary,
        'is_admin':         is_admin,
        'is_creator':       is_creator or is_participant,
        'today':            timezone.now().date(),
        'participant_expense_summary':      participant_expense_summary,
        'total_submitted':                  total_submitted,
        'all_submitted':                    all_submitted,
        'unregistered_travelers':           unregistered_travelers,
        'active_invites':                   active_invites,
        'all_users':                        User.objects.filter(is_active=True, is_approved=True, role='EMPLOYEE').order_by('last_name','first_name'),
        'completeness_percentage':          completeness_percentage,
        'liquidation_summary':              liquidation_summary,
        'missing_itinerary_participants':   missing_itinerary_participants,
        'unregistered_for_tag': unregistered_for_tag,
    }
    return render(request, 'travel_app/shared/travel_detail.html', context)

# ══════════════════════════════════════════════════════════════════════
# UPLOAD DOCUMENT
# ══════════════════════════════════════════════════════════════════════

@csrf_protect
@never_cache
def upload_document(request, pk):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    travel = get_object_or_404(TravelRecord, pk=pk)

    if request.method == 'POST':
        doc_type       = request.POST.get('doc_type')
        file           = request.FILES.get('file')
        notes          = request.POST.get('notes', '').strip()
        participant_id = request.POST.get('participant_id')

        if not doc_type or not file:
            from django.contrib import messages
            messages.error(request, 'Document type and file are required.')
            return redirect('travel_app:travel_detail', pk=pk)

        valid_types = [t for t, _ in ParticipantDocument.DOC_TYPE_CHOICES]
        if doc_type not in valid_types:
            from django.contrib import messages
            messages.error(request, 'Invalid document type.')
            return redirect('travel_app:travel_detail', pk=pk)

        if user.role in ['DEPT_SEC', 'CAMPUS_SEC', 'ADMIN']:
            participant = get_object_or_404(TravelParticipant, id=participant_id, travel_record=travel)
        else:
            participant = travel.participants.filter(user=user).first()
            if not participant:
                from django.contrib import messages
                messages.error(request, 'You are not a participant in this travel.')
                return redirect('travel_app:travel_detail', pk=pk)

        # Block duplicate ITINERARY or ACTUAL_ITINERARY uploads
        if doc_type in ['ITINERARY', 'ACTUAL_ITINERARY']:
            already_exists = ParticipantDocument.objects.filter(
                participant=participant,
                doc_type=doc_type
            ).exists()
            if already_exists:
                from django.contrib import messages
                label = 'Itinerary' if doc_type == 'ITINERARY' else 'Actual Itinerary'
                messages.error(request, f'{label} already uploaded. Use Replace to update it.')
                tab = request.POST.get('tab', '')
                url = f"/travel/travels/{pk}/" + (f"?tab={tab}" if tab else "")
                return redirect(url)

        doc = ParticipantDocument.objects.create(
            participant=participant,
            doc_type=doc_type,
            file=file,
            uploaded_by=user,
            notes=notes,
        )

        from django.contrib import messages
        messages.success(request, f'{doc.get_doc_type_display()} uploaded successfully.')

    tab = request.POST.get('tab', '')
    url = f"/travel/travels/{pk}/" + (f"?tab={tab}" if tab else "")
    return redirect(url)


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
 
        elif action == 'tag':
            # ── BLOCK if any participant is unregistered ──
            unregistered = travel.participants.filter(is_registered=False)
            if unregistered.exists():
                from django.contrib import messages
                names = ', '.join([p.name for p in unregistered])
                messages.error(
                    request,
                    f'Cannot tag budget yet. The following participant(s) are not matched to a registered user: {names}.'
                )
                return redirect('travel_app:travel_detail', pk=pk)
            # ── end block ──
            # ── BLOCK if any registered participant is missing itinerary amount ──
            participants_all = travel.participants.filter(is_registered=True).select_related('user')
            missing = []
            for tp in participants_all:
                has_amount = ParticipantDocument.objects.filter(
                    participant=tp,
                    doc_type='ITINERARY',
                    extracted_amount__isnull=False
                ).exists()
                if not has_amount:
                    missing.append(tp.get_display_name())
            
            
            if missing:
                from django.contrib import messages
                names = ', '.join(missing)
                messages.error(
                    request,
                    f'Cannot tag budget yet. The following participant(s) have not submitted '
                    f'an itinerary with an amount: {names}.'
                )
                return redirect('travel_app:travel_detail', pk=pk)
            # ── end block ──
 
            budget_source_id = request.POST.get('budget_source_id')
            try:
                source = BudgetSource.objects.get(id=budget_source_id, is_active=True)
 
                allowed = False
                if user.role == 'DEPT_SEC' and source.budget_scope == 'COLLEGE':
                    allowed = True
                elif user.role == 'CAMPUS_SEC' and source.budget_scope == 'CAMPUS':
                    allowed = True
 
                if not allowed:
                    from django.contrib import messages
                    messages.error(request, 'You cannot use this budget source.')
                else:
                    from django.utils import timezone as tz
                    from decimal import Decimal, InvalidOperation
 
                    raw_amount = request.POST.get('amount', '').strip().replace(',', '')
                    try:
                        amount_deducted = Decimal(raw_amount) if raw_amount else Decimal('0')
                        if amount_deducted < 0:
                            amount_deducted = Decimal('0')
                    except InvalidOperation:
                        amount_deducted = Decimal('0')
 
                    is_retag = travel.is_budget_tagged
                    old_source = travel.budget_source
                    old_amount = travel.amount_deducted or Decimal('0')
 
                    # ── STEP 1: Restore old source if re-tagging ──────────────
                    if is_retag and old_source:
                        participants = travel.participants.select_related('user').all()
                        participant_count = participants.count()
 
                        for tp in participants:
                            individual_amount = ParticipantDocument.objects.filter(
                                participant=tp,
                                doc_type__in=['BURS', 'ITINERARY'],
                                extracted_amount__isnull=False
                            ).order_by('-uploaded_at').values_list(
                                'extracted_amount', flat=True
                            ).first()
 
                            if individual_amount is not None:
                                restore_amount = Decimal(str(individual_amount))
                            else:
                                restore_amount = old_amount / participant_count if participant_count else Decimal('0')
 
                            try:
                                old_usage = BudgetUsage.objects.get(
                                    user=tp.user,
                                    budget_source=old_source,
                                    year=old_source.fiscal_year
                                )
                                old_usage.restore(restore_amount)
                            except BudgetUsage.DoesNotExist:
                                pass
 
                    # ── STEP 2: Update travel record ──────────────────────────
                    travel.budget_source    = source
                    travel.budget_tagged_by = user
                    travel.budget_tagged_at = tz.now()
                    travel.amount_deducted  = amount_deducted
                    travel.save(update_fields=[
                        'budget_source', 'budget_tagged_by',
                        'budget_tagged_at', 'amount_deducted'
                    ])
 
                    # ── STEP 3: Deduct from new source ────────────────────────
                    participants = travel.participants.select_related('user').all()
                    participant_count = participants.count()
 
                    if participant_count > 0:
                        for tp in participants:
                            individual_amount = ParticipantDocument.objects.filter(
                                participant=tp,
                                doc_type__in=['BURS', 'ITINERARY'],
                                extracted_amount__isnull=False
                            ).order_by('-uploaded_at').values_list(
                                'extracted_amount', flat=True
                            ).first()
 
                            if individual_amount is not None:
                                deduct_amount = Decimal(str(individual_amount))
                            else:
                                deduct_amount = amount_deducted / participant_count
 
                            usage, _ = source.get_or_create_usage(tp.user)
                            usage.deduct(deduct_amount)
 
                    from django.contrib import messages
                    if is_retag:
                        messages.success(
                            request,
                            f'Budget re-tagged to "{source.budget_name}". '
                            f'₱{old_amount:,.2f} returned to "{old_source.budget_name}", '
                            f'₱{amount_deducted:,.2f} deducted from new source.'
                        )
                    else:
                        messages.success(
                            request,
                            f'Budget source "{source.budget_name}" assigned. '
                            f'₱{amount_deducted:,.2f} deducted.'
                        )
 
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
            participants__college_name=user.college.name if user.college else ''
        ).distinct()
    elif user.role == 'CAMPUS_SEC':
        travels = TravelRecord.objects.filter(
            participants__campus_name=user.campus.name if user.campus else ''
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

    total = travels.count()

    # Pagination — 20 per page
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
    paginator = Paginator(travels, 20)
    page_num  = request.GET.get('page', 1)
    try:
        travels_page = paginator.page(page_num)
    except PageNotAnInteger:
        travels_page = paginator.page(1)
    except EmptyPage:
        travels_page = paginator.page(paginator.num_pages)

    context = {
        'user':          user,
        'travels':       travels_page,
        'today':         today,
        'total':         total,
        'filter_tagged': filter_tagged,
        'filter_scope':  filter_scope,
        'filter_year':   filter_year,
        'search':        search,
        'current_year':  today.year,
        'paginator':     paginator,
        'page_obj':      travels_page,
    }
    return render(request, 'travel_app/shared/all_travels.html', context)

# ══════════════════════════════════════════════════════════════════════
# MY TRAVELS + STATS
# ══════════════════════════════════════════════════════════════════════

@never_cache
@require_role(['EMPLOYEE'])
def my_travels(request, user=None):
    return redirect('travel_app:all_travels')


# ══════════════════════════════════════════════════════════════════════
# BUDGET (MERGED VIEW)
# ══════════════════════════════════════════════════════════════════════
@never_cache
def budget_view(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['ADMIN', 'DEPT_SEC', 'CAMPUS_SEC']:
        from django.contrib import messages
        messages.error(request, 'Access denied.')
        return redirect('accounts:dashboard')

    from accounts.models import College
    today = timezone.now().date()
    year  = int(request.GET.get('year', today.year))
    fiscal_year_choices = [today.year, today.year + 1]

    if request.method == 'POST' and user.role != 'ADMIN':
        action = request.POST.get('action')

        if action == 'create':
            budget_name   = request.POST.get('name', '').strip()
            budget_scope  = 'CAMPUS' if user.role == 'CAMPUS_SEC' else 'COLLEGE'
            budget_amount = request.POST.get('budget_amount', 0) or 0
            description   = request.POST.get('description', '').strip()
            fiscal_year   = int(request.POST.get('year', today.year))
            if not budget_name:
                from django.contrib import messages
                messages.error(request, 'Budget name is required.')
            elif fiscal_year not in fiscal_year_choices:
                from django.contrib import messages
                messages.error(request, 'Invalid fiscal year selected.')
            else:
                try:
                    BudgetSource.objects.create(
                        budget_name=budget_name,
                        budget_scope=budget_scope,
                        fiscal_year=fiscal_year,
                        budget_amount=budget_amount,
                        description=description,
                        college=user.college if user.role == 'DEPT_SEC' else None,
                    )
                    from django.contrib import messages
                    messages.success(request, f'Budget source "{budget_name}" created for FY {fiscal_year}.')
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
                status = 'activated' if source.is_active else 'deactivated'
                messages.success(request, f'"{source.budget_name}" {status}.')
            except BudgetSource.DoesNotExist:
                from django.contrib import messages
                messages.error(request, 'Budget source not found.')

        elif action == 'delete':
            source_id = request.POST.get('source_id')
            try:
                source = BudgetSource.objects.get(id=source_id)
                if source.travel_records.exists():
                    from django.contrib import messages
                    messages.error(request, f'Cannot delete "{source.budget_name}" — travels are using it.')
                else:
                    name = source.budget_name
                    source.delete()
                    from django.contrib import messages
                    messages.success(request, f'"{name}" deleted.')
            except BudgetSource.DoesNotExist:
                from django.contrib import messages
                messages.error(request, 'Budget source not found.')

        return redirect(f"{request.path}?year={year}")

    # Build source list
    if user.role == 'ADMIN':
        sources = BudgetSource.objects.filter(fiscal_year=year).order_by('budget_scope', 'budget_name')
    elif user.role == 'DEPT_SEC':
        sources = BudgetSource.objects.filter(
            fiscal_year=year, budget_scope='COLLEGE', college=user.college
        ).order_by('budget_name')
    else:
        sources = BudgetSource.objects.filter(
            fiscal_year=year, budget_scope='CAMPUS'
        ).order_by('budget_name')

    overview = []
    for source in sources:
        if user.role == 'ADMIN':
            usages = BudgetUsage.objects.filter(budget_source=source, year=year).select_related('user__college', 'user__campus')
            rows = [{
                'label':      u.user.college.name if u.user.college else (u.user.campus.name if u.user.campus else 'Unknown'),
                'user':       u.user.get_full_name(),
                'allocated':  u.allocated_amount,
                'used':       u.used_amount,
                'remaining':  u.remaining_amount,
                'percentage': u.usage_percentage,
                'status':     u.status,
            } for u in usages]
        else:
            usages = BudgetUsage.objects.filter(budget_source=source, year=year)
            rows = []

        total_used   = sum(u.used_amount for u in usages)
        total_alloc  = source.budget_amount
        tagged_count = source.travel_records.count()
        pct          = round((total_used / total_alloc * 100), 1) if total_alloc > 0 else 0
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

    context = {
        'user':                user,
        'today':               today,
        'current_year':        year,
        'year_range':          range(today.year - 1, today.year + 3),
        'fiscal_year_choices': fiscal_year_choices,
        'overview':            overview,
        'college_count':       College.objects.count(),
    }
    return render(request, 'travel_app/shared/budget.html', context)

# ══════════════════════════════════════════════════════════════════════
# MANAGE BUDGET SOURCES (Admin)
# ══════════════════════════════════════════════════════════════════════
# Replace your existing manage_budget_sources view with this

@csrf_protect
@never_cache
def manage_budget_sources(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['ADMIN', 'DEPT_SEC', 'CAMPUS_SEC']:
        from django.contrib import messages
        messages.error(request, 'Access denied.')
        return redirect('accounts:dashboard')

    from accounts.models import College
    today = timezone.now().date()
    year  = int(request.GET.get('year', today.year))

    # Fiscal year choices: current year + next year only
    fiscal_year_choices = [today.year, today.year + 1]

    if request.method == 'POST' and user.role != 'ADMIN':
        action = request.POST.get('action')

        if action == 'create':
            budget_name   = request.POST.get('name', '').strip()
            budget_scope  = 'CAMPUS' if user.role == 'CAMPUS_SEC' else 'COLLEGE'
            budget_amount = request.POST.get('budget_amount', 0) or 0
            description   = request.POST.get('description', '').strip()
            fiscal_year   = int(request.POST.get('year', today.year))

            if not budget_name:
                from django.contrib import messages
                messages.error(request, 'Budget name is required.')
            elif fiscal_year not in fiscal_year_choices:
                from django.contrib import messages
                messages.error(request, 'Invalid fiscal year selected.')
            else:
                try:
                    BudgetSource.objects.create(
                        budget_name=budget_name,
                        budget_scope=budget_scope,
                        fiscal_year=fiscal_year,
                        budget_amount=budget_amount,
                        description=description,
                        college=user.college if user.role == 'DEPT_SEC' else None,
                    )
                    from django.contrib import messages
                    messages.success(request, f'Budget source "{budget_name}" created for FY {fiscal_year}.')
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
                status = 'activated' if source.is_active else 'deactivated'
                messages.success(request, f'"{source.budget_name}" {status}.')
            except BudgetSource.DoesNotExist:
                from django.contrib import messages
                messages.error(request, 'Budget source not found.')

        elif action == 'delete':
            source_id = request.POST.get('source_id')
            try:
                source = BudgetSource.objects.get(id=source_id)
                if source.travel_records.exists():
                    from django.contrib import messages
                    messages.error(request, f'Cannot delete "{source.budget_name}" — travels are using it.')
                else:
                    name = source.budget_name
                    source.delete()
                    from django.contrib import messages
                    messages.success(request, f'"{name}" deleted.')
            except BudgetSource.DoesNotExist:
                from django.contrib import messages
                messages.error(request, 'Budget source not found.')

        return redirect(f"{request.path}?year={year}")

    # Build source list depending on role
    if user.role == 'ADMIN':
        sources = BudgetSource.objects.filter(fiscal_year=year).order_by('budget_scope', 'budget_name')
    elif user.role == 'DEPT_SEC':
        sources = BudgetSource.objects.filter(
            fiscal_year=year, budget_scope='COLLEGE'
        ).order_by('budget_name')
    else:  # CAMPUS_SEC
        sources = BudgetSource.objects.filter(
            fiscal_year=year, budget_scope='CAMPUS'
        ).order_by('budget_name')

    source_data = []
    for source in sources:
        usages     = BudgetUsage.objects.filter(budget_source=source, year=year)
        total_used = sum(u.used_amount for u in usages)
        # Amount is as-is — no multiplication
        allocated  = source.budget_amount
        remaining  = allocated - total_used
        pct        = round((total_used / allocated * 100), 1) if allocated > 0 else 0
        status     = 'exhausted' if pct >= 100 else 'critical' if pct >= 80 else 'warning' if pct >= 60 else 'healthy'
        source_data.append({
            'source':       source,
            'allocated':    allocated,
            'used':         total_used,
            'remaining':    remaining,
            'percentage':   pct,
            'status':       status,
            'travel_count': source.travel_records.count(),
        })

    context = {
        'user':                user,
        'today':               today,
        'current_year':        year,
        'year_range':          range(today.year - 1, today.year + 3),
        'fiscal_year_choices': fiscal_year_choices,
        'source_data':         source_data,
        'college_count':       College.objects.count(),
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

    from accounts.models import College
    today = timezone.now().date()
    year  = int(request.GET.get('year', today.year))

    if user.role == 'ADMIN':
        sources  = BudgetSource.objects.filter(fiscal_year=year, is_active=True).order_by('budget_scope', 'budget_name')
        overview = []
        for source in sources:
            usages = BudgetUsage.objects.filter(
                budget_source=source, year=year
            ).select_related('user__college', 'user__campus').order_by('user__last_name')

            if source.budget_scope == 'COLLEGE':
                rows = [{
                    'label':      u.user.college.name if u.user.college else 'Unknown',
                    'user':       u.user.get_full_name(),
                    'allocated':  u.allocated_amount,
                    'used':       u.used_amount,
                    'remaining':  u.remaining_amount,
                    'percentage': u.usage_percentage,
                    'status':     u.status,
                } for u in usages]
            else:
                rows = [{
                    'label':      u.user.campus.name if u.user.campus else 'Unknown',
                    'user':       u.user.get_full_name(),
                    'allocated':  u.allocated_amount,
                    'used':       u.used_amount,
                    'remaining':  u.remaining_amount,
                    'percentage': u.usage_percentage,
                    'status':     u.status,
                } for u in usages]

            # Always use source.budget_amount as the total — never sum usage rows
            total_alloc  = source.budget_amount
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
            'total_allocated': item['source'].budget_amount,  # always use source amount
            'total_used':      item.get('used', 0),
            'total_remaining': item['source'].budget_amount - item.get('used', 0),
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
            participants__college_name=user.college.name
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
            participants__campus_name=user.campus.name
        ).distinct()
    else:
        queue = []

    context = {
        'user':  user,
        'queue': queue,
        'today': timezone.now().date(),
    }
    return render(request, 'travel_app/secretary/queue.html', context)


# ══════════════════════════════════════════════════════════════════════
# DOWNLOAD ZIP
# ══════════════════════════════════════════════════════════════════════

@never_cache
def download_zip(request, pk):
    import zipfile
    import io
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

    if is_secretary or is_admin:
        documents = ParticipantDocument.objects.filter(
            participant__travel_record=travel
        ).select_related('participant__user')
    else:
        my_participant = travel.participants.filter(user=user).first()
        documents = ParticipantDocument.objects.filter(
            participant=my_participant
        ) if my_participant else ParticipantDocument.objects.none()

    if not documents.exists():
        from django.contrib import messages
        messages.error(request, 'No documents to download.')
        return redirect('travel_app:travel_detail', pk=pk)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
        for doc in documents:
            try:
                file_path = doc.file.path
                owner     = doc.participant.user.get_full_name()
                file_name = f"{owner}/{doc.get_doc_type_display()} - {os.path.basename(file_path)}"
                zf.write(file_path, arcname=file_name)
            except Exception:
                pass

    buffer.seek(0)
    zip_name = f"Travel_{travel.destination}_{travel.start_date}.zip".replace(' ', '_')
    response = HttpResponse(buffer, content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{zip_name}"'
    return response


# ══════════════════════════════════════════════════════════════════════
# CONFIRM / REJECT EXTRACTION
# ══════════════════════════════════════════════════════════════════════

@csrf_protect
@never_cache
def confirm_extraction(request, doc_id):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['DEPT_SEC', 'CAMPUS_SEC', 'ADMIN']:
        from django.contrib import messages
        messages.error(request, 'Access denied.')
        return redirect('accounts:dashboard')

    from django.utils import timezone as tz
    doc    = get_object_or_404(ParticipantDocument, id=doc_id)
    travel = doc.participant.travel_record

    if request.method == 'POST':
        doc.is_confirmed = True
        doc.confirmed_by = user
        doc.confirmed_at = tz.now()
        doc.save(update_fields=['is_confirmed', 'confirmed_by', 'confirmed_at'])

        from django.contrib import messages
        messages.success(request, 'Document confirmed.')

    return redirect('travel_app:travel_detail', pk=travel.id)


@csrf_protect
@never_cache
def reject_extraction(request, doc_id):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['DEPT_SEC', 'CAMPUS_SEC', 'ADMIN']:
        from django.contrib import messages
        messages.error(request, 'Access denied.')
        return redirect('accounts:dashboard')

    doc    = get_object_or_404(ParticipantDocument, id=doc_id)
    travel = doc.participant.travel_record

    if request.method == 'POST':
        doc.extracted_destination = ''
        doc.extracted_start_date  = None
        doc.extracted_end_date    = None
        doc.extracted_amount      = None
        doc.extracted_purpose     = ''
        doc.extraction_successful = False
        doc.save(update_fields=[
            'extracted_destination', 'extracted_start_date', 'extracted_end_date',
            'extracted_amount', 'extracted_purpose', 'extraction_successful',
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


# ══════════════════════════════════════════════════════════════════════
# EXTRACT TRAVEL ORDER (AJAX)
# ══════════════════════════════════════════════════════════════════════

@csrf_protect
@never_cache
def extract_travel_order_ajax(request):
    user = get_authenticated_user(request)
    if not user:
        return JsonResponse({'error': 'Not authenticated'}, status=401)

    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    file = request.FILES.get('file')
    if not file:
        return JsonResponse({'error': 'No file provided'}, status=400)

    allowed_extensions = {'.pdf', '.docx', '.doc', '.jpg', '.jpeg', '.png', '.xlsx'}
    ext = os.path.splitext(file.name)[1].lower()
    if ext not in allowed_extensions:
        return JsonResponse({'error': f'File type {ext} not supported'}, status=400)

    try:
        from django.core.files.storage import default_storage
        from django.core.files.base import ContentFile
        from django.db.models import Q
        from .ai_service import extract_text_from_file, _extract_travel_order

        temp_path = default_storage.save(
            f'travel_documents/temp/{file.name}',
            ContentFile(file.read())
        )
        full_path = default_storage.path(temp_path)

        text, method = extract_text_from_file(full_path)

        if not text or len(text.strip()) < 20:
            default_storage.delete(temp_path)
            return JsonResponse({
                'success': False,
                'error': 'Could not extract text from this file. Try a clearer scan or different format.'
            })

        result = _extract_travel_order(text)

        matched_travelers   = []
        unmatched_travelers = []

        if result and result.get('traveler_names'):
            HONORIFICS = {'dr.', 'dr', 'prof.', 'prof', 'mr.', 'mr', 'ms.', 'ms', 'mrs.', 'mrs', 'engr.', 'engr'}

            for name in result['traveler_names']:
                name = name.strip()
                if not name:
                    continue

                parts = name.split()
                parts = [p for p in parts if p.lower() not in HONORIFICS]

                query = Q()
                for part in parts:
                    if len(part) > 1:
                        query |= Q(first_name__icontains=part) | Q(last_name__icontains=part)

                match = None
                if query:
                    match = User.objects.filter(query, is_active=True, is_approved=True).first()

                if match:
                    matched_travelers.append({
                        'id':      match.id,
                        'name':    match.get_full_name(),
                        'college': match.college.name if match.college else '',
                        'matched': True,
                    })
                else:
                    unmatched_travelers.append({
                        'id':      None,
                        'name':    name,
                        'college': '',
                        'matched': False,
                    })

        request.session['temp_travel_order_path']  = temp_path
        request.session['temp_travel_order_names'] = result.get('traveler_names', []) if result else []

        return JsonResponse({
            'success':             True,
            'method':              method,
            'confidence':          result.get('confidence', 'low') if result else 'low',
            'destination':         result.get('destination', '') if result else '',
            'start_date':          result.get('start_date', '') if result else '',
            'end_date':            result.get('end_date', '') if result else '',
            'purpose':             result.get('purpose', '') if result else '',
            'matched_travelers':   matched_travelers,
            'unmatched_travelers': unmatched_travelers,
            'unmatched_count':     len(unmatched_travelers),
        })

    except Exception as e:
        logger.error(f"extract_travel_order_ajax error: {e}")
        return JsonResponse({'error': str(e)}, status=500)


# ══════════════════════════════════════════════════════════════════════
# CHANGE SCOPE
# ══════════════════════════════════════════════════════════════════════

@csrf_protect
@never_cache
def change_scope(request, pk):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    travel = get_object_or_404(TravelRecord, pk=pk)

    is_participant = travel.participants.filter(user=user).exists()
    is_creator     = travel.created_by == user
    is_secretary   = user.role in ['DEPT_SEC', 'CAMPUS_SEC']

    if not (is_participant or is_creator or is_secretary):
        from django.contrib import messages
        messages.error(request, 'You do not have permission to change the scope.')
        return redirect('travel_app:travel_detail', pk=pk)

    if request.method == 'POST':
        new_scope = request.POST.get('scope')
        if new_scope in ['COLLEGE', 'CAMPUS']:
            travel.scope = new_scope
            travel.save(update_fields=['scope'])
            from django.contrib import messages
            messages.success(request, f'Travel scope changed to {travel.get_scope_display()}.')
        else:
            from django.contrib import messages
            messages.error(request, 'Invalid scope.')

    return redirect('travel_app:travel_detail', pk=pk)


# ══════════════════════════════════════════════════════════════════════
# LOOKUP TRAVELER (AJAX)
# ══════════════════════════════════════════════════════════════════════

@csrf_protect
@never_cache
def lookup_traveler_ajax(request):
    user = get_authenticated_user(request)
    if not user:
        return JsonResponse({'error': 'Not authenticated'}, status=401)
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    name = request.POST.get('name', '').strip()
    if not name:
        return JsonResponse({'found': False})

    from django.db.models import Q
    HONORIFICS = {'dr.', 'dr', 'prof.', 'prof', 'mr.', 'mr', 'ms.', 'ms', 'mrs.', 'mrs', 'engr.', 'engr'}

    parts = [p for p in name.split() if p.lower() not in HONORIFICS]
    query = Q()
    for part in parts:
        if len(part) > 1:
            query |= Q(first_name__icontains=part) | Q(last_name__icontains=part)

    match = None
    if query:
        match = User.objects.filter(query, is_active=True, is_approved=True).first()

    if match:
        return JsonResponse({
            'found':   True,
            'id':      match.id,
            'name':    match.get_full_name(),
            'college': match.college.name if match.college else '',
        })
    return JsonResponse({'found': False})


# ══════════════════════════════════════════════════════════════════════
# SET DOCUMENT AMOUNT
# ══════════════════════════════════════════════════════════════════════

@csrf_protect
@never_cache
def set_document_amount(request, doc_id):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    doc    = get_object_or_404(ParticipantDocument, id=doc_id)
    travel = doc.participant.travel_record

    is_participant = travel.participants.filter(user=user).exists()
    is_secretary   = user.role in ['DEPT_SEC', 'CAMPUS_SEC']
    is_admin       = user.role == 'ADMIN'

    if not (is_participant or is_secretary or is_admin):
        from django.contrib import messages
        messages.error(request, 'You do not have permission to set this amount.')
        return redirect('travel_app:travel_detail', pk=travel.id)

    if doc.doc_type not in ('BURS', 'ITINERARY', 'ACTUAL_ITINERARY'):
        from django.contrib import messages
        messages.error(request, 'Amount can only be set on BURS, Itinerary, or Actual Itinerary documents.')
        return redirect('travel_app:travel_detail', pk=travel.id)
    
    # Lock: block amount change if budget is tagged and this participant is liquidated
    if doc.doc_type in ('ITINERARY', 'ACTUAL_ITINERARY'):
        participant = doc.participant
        is_liquidated = (
            travel.is_budget_tagged and
            participant.documents.filter(
                doc_type='ACTUAL_ITINERARY',
                is_confirmed=True
            ).exists()
        )
        if is_liquidated:
            from django.contrib import messages
            messages.error(request, 'Cannot change amount. Liquidation has already been confirmed for this participant.')
            tab = request.POST.get('tab', '')
            url = f"/travel/travels/{travel.id}/" + (f"?tab={tab}" if tab else "")
            return redirect(url)

    if request.method == 'POST':
        from decimal import Decimal, InvalidOperation
        raw = request.POST.get('amount', '').strip().replace(',', '')
        try:
            amount = Decimal(raw)
            if amount < 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            from django.contrib import messages
            messages.error(request, 'Invalid amount.')
            return redirect('travel_app:travel_detail', pk=travel.id)

        doc.extracted_amount = amount
        doc.save(update_fields=['extracted_amount'])
        if doc.doc_type == 'ITINERARY':
            from decimal import Decimal
            travel.amount_deducted = Decimal(str(amount))
            travel.save(update_fields=['amount_deducted'])

        from django.contrib import messages

        # If this is an actual itinerary and budget is already tagged, liquidate
        if doc.doc_type == 'ACTUAL_ITINERARY' and travel.is_budget_tagged:
            from .budget_service import liquidate_participant
            result = liquidate_participant(doc.participant, amount)
            if result['success']:
                doc.is_confirmed = True
                doc.confirmed_by = user
                doc.confirmed_at = timezone.now()
                doc.save(update_fields=['is_confirmed', 'confirmed_by', 'confirmed_at'])
                from decimal import Decimal
                travel.amount_deducted = Decimal(str(result['actual_amount']))
                travel.save(update_fields=['amount_deducted'])
                messages.success(request, f'Amount saved. Liquidation: {result["message"]}')
            else:
                messages.warning(request, f'Amount saved but liquidation skipped: {result["message"]}')
        else:
            messages.success(request, f'Amount of ₱{amount:,.2f} saved.')

    tab = request.POST.get('tab', '')
    url = f"/travel/travels/{travel.id}/" + (f"?tab={tab}" if tab else "")
    return redirect(url)


# ══════════════════════════════════════════════════════════════════════
# REPLACE DOCUMENT
# ══════════════════════════════════════════════════════════════════════

@csrf_protect
@never_cache
def replace_document(request, doc_id):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    doc    = get_object_or_404(ParticipantDocument, id=doc_id)
    travel = doc.participant.travel_record

    is_participant = travel.participants.filter(user=user).exists()
    is_secretary   = user.role in ['DEPT_SEC', 'CAMPUS_SEC']
    is_admin       = user.role == 'ADMIN'

    if not (is_participant or is_secretary or is_admin):
        from django.contrib import messages
        messages.error(request, 'You cannot replace documents on this travel.')
        return redirect('travel_app:travel_detail', pk=travel.id)

    if doc.doc_type == 'TRAVEL_ORDER':
        from django.contrib import messages
        messages.error(request, 'Travel Order cannot be replaced.')
        return redirect('travel_app:travel_detail', pk=travel.id)
    
    # Lock: block replace if budget is tagged and this participant is liquidated
    if doc.doc_type in ('ITINERARY', 'ACTUAL_ITINERARY'):
        participant = doc.participant
        is_liquidated = (
            travel.is_budget_tagged and
            participant.documents.filter(
                doc_type='ACTUAL_ITINERARY',
                is_confirmed=True
            ).exists()
        )
        if is_liquidated:
            from django.contrib import messages
            messages.error(request, 'This document is locked. Liquidation has already been confirmed for this participant.')
            tab = request.POST.get('tab', '')
            url = f"/travel/travels/{travel.id}/" + (f"?tab={tab}" if tab else "")
            return redirect(url)

    if request.method == 'POST':
        file = request.FILES.get('file')
        if not file:
            from django.contrib import messages
            messages.error(request, 'No file provided.')
            return redirect('travel_app:travel_detail', pk=travel.id)

        doc.file        = file
        doc.uploaded_by = user
        doc.uploaded_at = timezone.now()
        # Reset confirmation if ACTUAL_ITINERARY replaced — forces re-liquidation
        if doc.doc_type == 'ACTUAL_ITINERARY':
            doc.is_confirmed = False
            doc.confirmed_by = None
            doc.confirmed_at = None
            doc.extracted_amount = None
            doc.save(update_fields=['file', 'uploaded_by', 'uploaded_at', 'is_confirmed', 'confirmed_by', 'confirmed_at', 'extracted_amount'])
        else:
            doc.save(update_fields=['file', 'uploaded_by', 'uploaded_at'])

        from django.contrib import messages
        messages.success(request, f'{doc.get_doc_type_display()} replaced successfully.')

    tab = request.POST.get('tab', '')
    url = f"/travel/travels/{travel.id}/" + (f"?tab={tab}" if tab else "")
    return redirect(url)


# ══════════════════════════════════════════════════════════════════════
# LIQUIDATION CALCULATOR
# ══════════════════════════════════════════════════════════════════════

@never_cache
def liquidation_calculator(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    if user.role == 'EMPLOYEE':
        travels = TravelRecord.objects.filter(participants__user=user).distinct()
    elif user.role == 'DEPT_SEC':
        travels = TravelRecord.objects.filter(
            scope='COLLEGE',
            participants__college_name=user.college.name if user.college else ''
        ).distinct()
    elif user.role == 'CAMPUS_SEC':
        travels = TravelRecord.objects.filter(
            participants__campus_name=user.campus.name if user.campus else ''
        ).distinct()
    else:  # ADMIN
        travels = TravelRecord.objects.all()

    travels = travels.filter(budget_source__isnull=False).order_by('-start_date')

    selected_travel = None
    result          = None
    selected_id     = request.GET.get('travel_id') or request.POST.get('travel_id')
    actual_amount   = request.POST.get('actual_amount', '').strip()

    if selected_id:
        try:
            selected_travel = travels.get(id=selected_id)
        except TravelRecord.DoesNotExist:
            selected_travel = None

    if selected_travel and actual_amount:
        from decimal import Decimal, InvalidOperation
        try:
            actual = Decimal(actual_amount.replace(',', ''))
            tagged = selected_travel.amount_deducted or Decimal('0')
            diff   = actual - tagged

            if diff > 0:
                result = {
                    'type':    'reimbursement',
                    'label':   'Reimbursement',
                    'message': f'Actual spending exceeded the budget by ₱{diff:,.2f}. The employee may file a reimbursement request.',
                    'diff':    diff,
                    'actual':  actual,
                    'tagged':  tagged,
                }
            elif diff < 0:
                result = {
                    'type':    'refund',
                    'label':   'Refund Due',
                    'message': f'Actual spending was ₱{abs(diff):,.2f} less than the budget. The employee must refund this amount to the school.',
                    'diff':    abs(diff),
                    'actual':  actual,
                    'tagged':  tagged,
                }
            else:
                result = {
                    'type':    'settled',
                    'label':   'Settled',
                    'message': 'Actual spending matches the budget exactly. No refund or reimbursement needed.',
                    'diff':    Decimal('0'),
                    'actual':  actual,
                    'tagged':  tagged,
                }
        except InvalidOperation:
            result = None

    context = {
        'user':            user,
        'travels':         travels,
        'selected_travel': selected_travel,
        'selected_id':     selected_id,
        'actual_amount':   actual_amount,
        'result':          result,
        'today':           timezone.now().date(),
    }
    return render(request, 'travel_app/shared/liquidation_calculator.html', context)

# ══════════════════════════════════════════════════════════════════════
# INVITE UNREGISTERED PARTICIPANT
# Add these to travel_app/views.py
# ══════════════════════════════════════════════════════════════════════

@csrf_protect
@never_cache
def invite_participant(request, pk):
    """Secretary matches an unregistered participant to a registered user."""
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['DEPT_SEC', 'CAMPUS_SEC']:
        from django.contrib import messages
        messages.error(request, 'Only secretaries can match participants.')
        return redirect('travel_app:travel_detail', pk=pk)

    travel = get_object_or_404(TravelRecord, pk=pk)

    if request.method == 'POST':
        unregistered_name = request.POST.get('unregistered_name', '').strip()
        matched_user_id   = request.POST.get('matched_user_id', '').strip()

        if not unregistered_name or not matched_user_id:
            from django.contrib import messages
            messages.error(request, 'Please select a user to match.')
            return redirect('travel_app:travel_detail', pk=pk)

        try:
            matched_user = User.objects.get(id=matched_user_id, is_active=True, is_approved=True)
            TravelParticipant.objects.filter(
                travel_record=travel,
                is_registered=False,
                name__iexact=unregistered_name
            ).delete()
            TravelParticipant.objects.get_or_create(
                travel_record=travel,
                user=matched_user,
                defaults={
                    'is_registered': True,
                    'college_name': matched_user.college.name if matched_user.college else '',
                    'campus_name': matched_user.campus.name if matched_user.campus else '',
                }
            )
            travel.refresh_scope()
            from django.contrib import messages
            messages.success(request, f'{unregistered_name} matched to {matched_user.get_full_name()} successfully.')
        except User.DoesNotExist:
            from django.contrib import messages
            messages.error(request, 'Selected user not found.')

    return redirect('travel_app:travel_detail', pk=pk)

# ══════════════════════════════════════════════════════════════════════
# AUTO-LINK AFTER APPROVAL
# Add this function and call it from accounts approve_user view
# ══════════════════════════════════════════════════════════════════════

def link_invited_user_to_travels(accepted_user):
    """
    Called after an invited user is approved.
    Links them to their travel records and transfers any pre-uploaded documents.
    """
    from .models import TravelInvite, TravelParticipant, ParticipantDocument

    invites = TravelInvite.objects.filter(
        accepted_by=accepted_user,
        is_used=True
    ).select_related('travel')

    for invite in invites:
        travel = invite.travel

        # Create TravelParticipant if not already exists
        participant, created = TravelParticipant.objects.get_or_create(
            travel_record=travel,
            user=accepted_user,
        )

        # Transfer any documents that were uploaded under this invite
        # Documents uploaded before registration are stored with a temp note
        ParticipantDocument.objects.filter(
            notes__contains=f'[INVITE:{invite.token}]'
        ).update(participant=participant)

        # Refresh travel scope
        travel.refresh_scope()

# ══════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# Add these views to the bottom of travel_app/views.py
# ══════════════════════════════════════════════════════════════════════

@never_cache
def notifications_list(request):
    """Full notifications page — all notifications for the user."""
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    notifications = Notification.objects.filter(
        user=user
    ).select_related('travel_record').order_by('-created_at')

    # Mark all as read when the page is visited
    Notification.objects.filter(user=user, is_read=False).update(is_read=True)

    context = {
        'user':          user,
        'notifications': notifications,
        'today':         timezone.now().date(),
    }
    return render(request, 'travel_app/shared/notifications.html', context)


@csrf_protect
@never_cache
def mark_notification_read(request, notif_id):
    """Mark a single notification as read and redirect to its travel."""
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    notif = get_object_or_404(Notification, id=notif_id, user=user)
    notif.is_read = True
    notif.save(update_fields=['is_read'])

    if notif.travel_record:
        return redirect('travel_app:travel_detail', pk=notif.travel_record.id)
    return redirect('travel_app:notifications_list')


@csrf_protect
@never_cache
def mark_all_notifications_read(request):
    """Mark all notifications as read for the current user."""
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    if request.method == 'POST':
        Notification.objects.filter(user=user, is_read=False).update(is_read=True)

    return redirect('travel_app:notifications_list')


# ══════════════════════════════════════════════════════════════════════
# REPORTS
# ══════════════════════════════════════════════════════════════════════
@never_cache
def reports_view(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    from django.db.models import Count, Sum, Avg
    from django.db.models.functions import TruncMonth

    today = timezone.now().date()
    year = int(request.GET.get('year', today.year))

    # --- Scope travels by role ---
    if user.role == 'EMPLOYEE':
        travels = TravelRecord.objects.filter(
            participants__user=user
        ).distinct()
    elif user.role == 'DEPT_SEC':
        travels = TravelRecord.objects.filter(
            participants__college_name=user.college.name if user.college else ''
        ).distinct()
    elif user.role == 'CAMPUS_SEC':
        travels = TravelRecord.objects.filter(
            participants__campus_name=user.campus.name if user.campus else ''
        ).distinct()
    else:  # ADMIN
        travels = TravelRecord.objects.all()

    travels_year = travels.filter(start_date__year=year)

    # --- Summary counts ---
    total_travels     = travels_year.count()
    total_this_month  = travels_year.filter(start_date__month=today.month).count()
    out_of_province   = travels_year.filter(is_out_of_province=True).count()
    untagged_count    = travels_year.filter(budget_source__isnull=True).count()

    # --- Monthly travel volume ---
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
    monthly_data = [monthly_counts[i] for i in range(1, 13)]

    # --- Budget summary ---
    budget_sources = get_sources_for_secretary(user, year=year)
    total_budget_used = sum(item.get('used', 0) for item in budget_sources)

    # --- Top destinations ---
    top_destinations = (
        travels_year
        .values('destination')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )

    # --- Top travelers (not for employee) ---
    top_travelers = []
    if user.role != 'EMPLOYEE':
        top_travelers = (
            TravelParticipant.objects
            .filter(travel_record__in=travels_year)
            .values('user__first_name', 'user__last_name', 'college_name')
            .annotate(count=Count('id'))
            .order_by('-count')[:8]
        )

    # --- Anomalies ---
    anomalies = []
    no_docs = travels_year.annotate(
        doc_count=Count('participants__documents')
    ).filter(doc_count=0)
    if no_docs.exists():
        anomalies.append({
            'type':   'warning',
            'icon':   'bi-folder-x',
            'title':  f'{no_docs.count()} travel(s) with no documents uploaded',
            'detail': ', '.join([str(t.destination) for t in no_docs[:3]]) +
                      ('...' if no_docs.count() > 3 else ''),
        })
    if untagged_count:
        anomalies.append({
            'type':   'info',
            'icon':   'bi-tag',
            'title':  f'{untagged_count} travel(s) with no budget tagged',
            'detail': '',
        })

    available_years = sorted(
        TravelRecord.objects.dates('start_date', 'year', order='DESC')
        .values_list('start_date__year', flat=True).distinct(),
        reverse=True
    ) or [today.year]
    # --- Available faculty for dropdown ---
    if user.role == 'EMPLOYEE':
        available_faculty = []
    elif user.role == 'DEPT_SEC':
        available_faculty = User.objects.filter(
            college=user.college, is_active=True, is_approved=True
        ).order_by('last_name', 'first_name')
    elif user.role == 'CAMPUS_SEC':
        available_faculty = User.objects.filter(
            campus=user.campus, is_active=True, is_approved=True
        ).order_by('last_name', 'first_name')
    else:
        available_faculty = User.objects.filter(
            is_active=True, is_approved=True
        ).order_by('last_name', 'first_name')

    # --- Available budget sources for dropdown ---
    if user.role == 'DEPT_SEC':
        available_sources = BudgetSource.objects.filter(fiscal_year=year, budget_scope='COLLEGE', college=user.college)
    elif user.role == 'CAMPUS_SEC':
        available_sources = BudgetSource.objects.filter(fiscal_year=year, budget_scope__in=['COLLEGE','CAMPUS'])
    else:
        available_sources = BudgetSource.objects.filter(fiscal_year=year)

    # --- Months list ---
    MONTH_NAMES = ['January','February','March','April','May','June',
                   'July','August','September','October','November','December']
    months = [{'num': i+1, 'name': MONTH_NAMES[i]} for i in range(12)]
    current_month = today.month

    employee_travels = []
    employee_total = 0
    employee_paginator = None
    if user.role == 'EMPLOYEE':
        emp_qs = travels
        if request.GET.get('start_date'):
            emp_qs = emp_qs.filter(start_date__gte=request.GET.get('start_date'))
        if request.GET.get('end_date'):
            emp_qs = emp_qs.filter(start_date__lte=request.GET.get('end_date'))
        from django.core.paginator import Paginator
        emp_ordered = emp_qs.order_by('start_date')
        emp_rows = []
        employee_total = 0
        for t in emp_ordered:
            participant = t.participants.filter(user=user).first()
            itinerary = None
            if participant:
                itinerary = participant.documents.filter(
                    doc_type='ACTUAL_ITINERARY', is_confirmed=True
                ).first() or participant.documents.filter(
                    doc_type='ITINERARY', extracted_amount__isnull=False
                ).first()
            amount = float(itinerary.extracted_amount) if itinerary and itinerary.extracted_amount else 0
            if request.GET.get('show_amounts'):
                employee_total += amount
            emp_rows.append({
                'purpose': t.purpose,
                'start_date': t.start_date,
                'end_date': t.end_date,
                'destination': t.destination,
                'amount_deducted': amount,
            })
        emp_paginator = Paginator(emp_rows, 10)
        try:
            employee_travels = emp_paginator.page(request.GET.get('page', 1))
        except Exception:
            employee_travels = emp_paginator.page(1)
        employee_paginator = emp_paginator

    # --- Records by faculty ---
    records_by_faculty = []
    if user.role != 'EMPLOYEE':
        rec_faculty_id = request.GET.get('faculty', 'all')
        rec_start      = request.GET.get('rec_start', '')
        rec_end        = request.GET.get('rec_end', '')
        for f in available_faculty:
            if rec_faculty_id != 'all' and str(f.id) != rec_faculty_id:
                continue
            fq = TravelRecord.objects.filter(participants__user=f).distinct().order_by('start_date')
            if rec_start:
                fq = fq.filter(start_date__gte=rec_start)
            if rec_end:
                fq = fq.filter(start_date__lte=rec_end)
            if fq.exists():
                from django.core.paginator import Paginator
                paged = Paginator(list(fq), 10)
                try:
                    page_obj = paged.page(request.GET.get(f'page_{f.id}', 1))
                except Exception:
                    page_obj = paged.page(1)
                records_by_faculty.append({
                    'name':      f.get_full_name(),
                    'travels':   page_obj,
                    'paginator': paged,
                    'fid':       f.id,
                })

    # --- Budget blocks ---
    budget_blocks = []
    if user.role != 'EMPLOYEE':
        bud_month     = int(request.GET.get('month', today.month))
        bud_source_id = request.GET.get('budget_source', 'all')
        src_qs = available_sources
        if bud_source_id != 'all':
            src_qs = src_qs.filter(id=bud_source_id)
        for source in src_qs:
            src_travels = travels.filter(
                budget_source=source,
                start_date__year=year,
                start_date__month=bud_month,
            )
            rows = []
            total = 0
            for t in src_travels:
                for p in t.participants.filter(user__isnull=False):
                    itinerary = p.documents.filter(
                        doc_type='ACTUAL_ITINERARY', is_confirmed=True
                    ).first() or p.documents.filter(
                        doc_type='ITINERARY', extracted_amount__isnull=False
                    ).first()
                    amount = float(itinerary.extracted_amount) if itinerary and itinerary.extracted_amount else 0
                    total += amount
                    rows.append({
                        'name':        p.get_display_name(),
                        'dates':       f"{t.start_date}" if not t.end_date else f"{t.start_date} – {t.end_date}",
                        'destination': t.destination,
                        'purpose':     t.purpose[:60],
                        'amount':      f'{amount:,.2f}',
                    })
            budget_blocks.append({
                'name':    source.budget_name,
                'budget':  float(source.budget_amount),
                'total':   total,
                'balance': float(source.budget_amount) - total,
                'rows':    rows,
            })

    context = {
        'user':              user,
        'today':             today,
        'selected_year':     year,
        'available_years':   available_years,
        'total_travels':     total_travels,
        'total_this_month':  total_this_month,
        'out_of_province':   out_of_province,
        'untagged_count':    untagged_count,
        'monthly_labels':    monthly_labels,
        'monthly_data':      monthly_data,
        'budget_sources':    budget_sources,
        'total_budget_used': total_budget_used,
        'top_destinations':  top_destinations,
        'top_travelers':     top_travelers,
        'anomalies':         anomalies,
        'available_faculty':  available_faculty,
        'available_sources':  available_sources,
        'months':             months,
        'current_month':      current_month,
        'employee_travels':    employee_travels,
        'employee_total':      employee_total,
        'records_by_faculty':  records_by_faculty,
        'budget_blocks':       budget_blocks,
        'employee_paginator': employee_paginator,
    }
    return render(request, 'travel_app/shared/reports.html', context)


# ══════════════════════════════════════════════════════════════════════
# DUPLICATE PARTICIPANT TRAVEL CHECK
# ══════════════════════════════════════════════════════════════════════
def get_overlapping_participants(participant_ids, start_date, end_date, exclude_travel_id=None):
    from django.db.models import Q
    overlaps = []
    end = end_date or start_date
    for pid in participant_ids:
        try:
            p = User.objects.get(id=pid)
        except User.DoesNotExist:
            continue
        qs = TravelParticipant.objects.filter(
            user=p
        ).filter(
            Q(travel_record__start_date__lte=end) &
            (Q(travel_record__end_date__gte=start_date) |
             Q(travel_record__end_date__isnull=True, travel_record__start_date__gte=start_date))
        ).select_related('travel_record')
        if exclude_travel_id:
            qs = qs.exclude(travel_record_id=exclude_travel_id)
        for tp in qs:
            overlaps.append({
                'name': p.get_full_name(),
                'destination': tp.travel_record.destination,
                'start_date': tp.travel_record.start_date,
            })
    return overlaps

# ══════════════════════════════════════════════════════════════════════
# PDF REPORTS
# ══════════════════════════════════════════════════════════════════════
import io
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from django.http import HttpResponse

@never_cache
def generate_budget_report(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role == 'EMPLOYEE':
        return redirect('travel_app:reports')

    from django.db.models import Sum, Q
    from datetime import date

    month      = int(request.GET.get('month', date.today().month))
    year       = int(request.GET.get('year',  date.today().year))
    source_id  = request.GET.get('budget_source', 'all')

    # Scope travels by role
    if user.role == 'DEPT_SEC':
        travels = TravelRecord.objects.filter(
            participants__college_name=user.college.name if user.college else ''
        ).distinct()
    elif user.role == 'CAMPUS_SEC':
        travels = TravelRecord.objects.filter(
            participants__campus_name=user.campus.name if user.campus else ''
        ).distinct()
    else:
        travels = TravelRecord.objects.all()

    travels = travels.filter(start_date__year=year, start_date__month=month)

    # Get budget sources in scope
    if user.role == 'DEPT_SEC':
        sources = BudgetSource.objects.filter(
            fiscal_year=year,
            budget_scope='COLLEGE'
        )
    elif user.role == 'CAMPUS_SEC':
        sources = BudgetSource.objects.filter(
            fiscal_year=year,
            budget_scope__in=['COLLEGE', 'CAMPUS']
        )
    else:
        sources = BudgetSource.objects.filter(fiscal_year=year)

    if source_id != 'all':
        sources = sources.filter(id=source_id)

    # Build PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)

    title_style = ParagraphStyle('title', fontSize=16, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=2)
    date_style  = ParagraphStyle('date',  fontSize=11, fontName='Helvetica',      alignment=TA_CENTER, spaceAfter=20)
    src_style   = ParagraphStyle('src',   fontSize=9,  fontName='Helvetica-Bold', alignment=TA_LEFT,   spaceAfter=4)

    MONTH_NAMES = ['','January','February','March','April','May','June',
                   'July','August','September','October','November','December']

    story = []
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("Travel Reports", title_style))
    story.append(Paragraph(f"As of {MONTH_NAMES[month]} {year}", date_style))

    col_headers = ['No.', 'Name of Traveler', 'Date of Travel', 'Destination', 'Purpose / Title', 'Amount Used (₱)']
    col_widths  = [1.2*cm, 5.5*cm, 4*cm, 4*cm, 6.5*cm, 3.8*cm]

    def table_style():
        return TableStyle([
            ('BACKGROUND',    (0,0),  (-1,0),  colors.HexColor('#1a3a6b')),
            ('TEXTCOLOR',     (0,0),  (-1,0),  colors.white),
            ('FONTNAME',      (0,0),  (-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',      (0,0),  (-1,-1), 8),
            ('ALIGN',         (0,0),  (-1,0),  'CENTER'),
            ('ALIGN',         (0,1),  (0,-1),  'CENTER'),
            ('ALIGN',         (-1,1), (-1,-1), 'RIGHT'),
            ('VALIGN',        (0,0),  (-1,-1), 'MIDDLE'),
            ('ROWBACKGROUND', (0,1),  (-1,-3), [colors.white, colors.HexColor('#f0f4ff')]),
            ('FONTNAME',      (0,-2), (-1,-1), 'Helvetica-Bold'),
            ('LINEABOVE',     (0,-2), (-1,-2), 1, colors.HexColor('#1a3a6b')),
            ('GRID',          (0,0),  (-1,-1), 0.5, colors.grey),
            ('TOPPADDING',    (0,0),  (-1,-1), 5),
            ('BOTTOMPADDING', (0,0),  (-1,-1), 5),
            ('LEFTPADDING',   (0,0),  (-1,-1), 4),
            ('RIGHTPADDING',  (0,0),  (-1,-1), 4),
        ])

    for source in sources:
        source_travels = travels.filter(budget_source=source)
        if not source_travels.exists():
            continue

        # Source header
        src_header = Table(
            [[f'Budget Source: {source.budget_name}', f'Budget: \u20b1{source.budget_amount:,.2f}']],
            colWidths=[16*cm, 9*cm]
        )
        src_header.setStyle(TableStyle([
            ('FONTNAME',      (0,0), (-1,-1), 'Helvetica-Bold'),
            ('FONTSIZE',      (0,0), (-1,-1), 9),
            ('ALIGN',         (1,0), (1,0),   'RIGHT'),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ]))
        story.append(src_header)

        rows = []
        total = 0
        for i, travel in enumerate(source_travels, 1):
            participants = travel.participants.filter(user__isnull=False)
            for p in participants:
                itinerary = p.documents.filter(
                    doc_type='ACTUAL_ITINERARY', is_confirmed=True
                ).first() or p.documents.filter(doc_type='ITINERARY').first()
                amount = itinerary.extracted_amount if itinerary and itinerary.extracted_amount else 0
                total += float(amount)
                rows.append([
                    str(i),
                    p.get_display_name(),
                    str(travel.start_date) if not travel.end_date else f"{travel.start_date} - {travel.end_date}",
                    travel.destination,
                    travel.purpose[:60],
                    f'{float(amount):,.2f}',
                ])

        if not rows:
            rows.append(['—', '—', '—', '—', 'No participant records', '0.00'])

        budget_val = float(source.budget_amount)
        balance    = budget_val - total
        table_data = [col_headers] + rows
        table_data.append(['', '', '', '', 'Total Used',        f'{total:,.2f}'])
        table_data.append(['', '', '', '', 'Remaining Balance', f'{balance:,.2f}'])

        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(table_style())
        story.append(t)
        story.append(Spacer(1, 0.7*cm))

    # Signature
    story.append(Spacer(1, 0.5*cm))
    sig_data = [
        ['Prepared by:'],
        [''],
        [''],
        ['_________________________'],
        [user.get_full_name()],
        [user.get_role_display() if hasattr(user, 'get_role_display') else user.role],
    ]
    sig_table = Table(sig_data, colWidths=[7*cm])
    sig_table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('ALIGN',    (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (0,0),   'Helvetica-Bold'),
    ]))
    story.append(sig_table)

    doc.build(story)
    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="budget_report_{MONTH_NAMES[month]}_{year}.pdf"'
    return response


@never_cache
def generate_travel_records(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    from datetime import date, datetime

    faculty_id   = request.GET.get('faculty', 'all')
    start_date   = request.GET.get('start_date', '')
    end_date     = request.GET.get('end_date', '')
    show_amounts = request.GET.get('show_amounts') == 'yes'

    # Scope by role
    if user.role == 'EMPLOYEE':
        faculty_users = [user]
    elif user.role == 'DEPT_SEC':
        faculty_users = list(User.objects.filter(
            college=user.college, is_active=True, is_approved=True
        ).order_by('last_name', 'first_name'))
    elif user.role == 'CAMPUS_SEC':
        faculty_users = list(User.objects.filter(
            campus=user.campus, is_active=True, is_approved=True
        ).order_by('last_name', 'first_name'))
    else:
        faculty_users = list(User.objects.filter(
            is_active=True, is_approved=True
        ).order_by('last_name', 'first_name'))

    if faculty_id != 'all' and user.role != 'EMPLOYEE':
        faculty_users = [u for u in faculty_users if str(u.id) == faculty_id]

    # Base travel filter
    def get_travels(faculty_user):
        qs = TravelRecord.objects.filter(
            participants__user=faculty_user
        ).distinct().order_by('start_date')
        if start_date:
            qs = qs.filter(start_date__gte=start_date)
        if end_date:
            qs = qs.filter(start_date__lte=end_date)
        return qs

    # Build PDF
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=1.5*cm, rightMargin=1.5*cm,
                            topMargin=1.5*cm, bottomMargin=1.5*cm)

    title_style   = ParagraphStyle('title', fontSize=16, fontName='Helvetica-Bold', alignment=TA_CENTER, spaceAfter=4)
    faculty_style = ParagraphStyle('fac',   fontSize=10, fontName='Helvetica-Bold', alignment=TA_LEFT,   spaceAfter=4)

    story = []
    story.append(Spacer(1, 0.3*cm))
    story.append(Paragraph("Travel Records", title_style))

    date_str = ''
    if start_date and end_date:
        date_str = f"{start_date} to {end_date}"
    elif start_date:
        date_str = f"From {start_date}"
    elif end_date:
        date_str = f"Up to {end_date}"
    else:
        date_str = "All Dates"

    story.append(Paragraph(date_str,
        ParagraphStyle('date', fontSize=11, fontName='Helvetica', alignment=TA_CENTER, spaceAfter=20)))

    if show_amounts and user.role == 'EMPLOYEE':
        col_headers = ['No.', 'Purpose / Title', 'Date of Travel', 'Destination', 'Amount (₱)']
        col_widths  = [1.2*cm, 8*cm, 5*cm, 6.5*cm, 3.5*cm]
    else:
        col_headers = ['No.', 'Purpose / Title', 'Date of Travel', 'Destination']
        col_widths  = [1.2*cm, 10*cm, 5*cm, 8*cm]

    def make_travel_table(rows, show_total=False, total=0):
        table_data = [col_headers] + rows
        if show_total:
            if show_amounts and user.role == 'EMPLOYEE':
                table_data.append(['', '', '', 'Total Spent', f'{total:,.2f}'])
        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        style = [
            ('BACKGROUND',    (0,0),  (-1,0),  colors.HexColor('#1a3a6b')),
            ('TEXTCOLOR',     (0,0),  (-1,0),  colors.white),
            ('FONTNAME',      (0,0),  (-1,0),  'Helvetica-Bold'),
            ('FONTSIZE',      (0,0),  (-1,-1), 8),
            ('ALIGN',         (0,0),  (-1,0),  'CENTER'),
            ('ALIGN',         (0,1),  (0,-1),  'CENTER'),
            ('VALIGN',        (0,0),  (-1,-1), 'MIDDLE'),
            ('ROWBACKGROUND', (0,1),  (-1,-2 if show_total else -1), [colors.white, colors.HexColor('#f0f4ff')]),
            ('GRID',          (0,0),  (-1,-1), 0.5, colors.grey),
            ('TOPPADDING',    (0,0),  (-1,-1), 5),
            ('BOTTOMPADDING', (0,0),  (-1,-1), 5),
            ('LEFTPADDING',   (0,0),  (-1,-1), 4),
            ('RIGHTPADDING',  (0,0),  (-1,-1), 4),
        ]
        if show_total:
            style += [
                ('FONTNAME',  (0,-1), (-1,-1), 'Helvetica-Bold'),
                ('LINEABOVE', (0,-1), (-1,-1), 1, colors.HexColor('#1a3a6b')),
                ('ALIGN',     (-1,-1),(-1,-1), 'RIGHT'),
            ]
        t.setStyle(TableStyle(style))
        return t

    for faculty_user in faculty_users:
        travels = get_travels(faculty_user)
        if not travels.exists():
            continue

        story.append(Paragraph(f"Faculty: {faculty_user.get_full_name()}", faculty_style))

        rows = []
        total = 0
        for i, travel in enumerate(travels, 1):
            if show_amounts and user.role == 'EMPLOYEE':
                participant = travel.participants.filter(user=faculty_user).first()
                itinerary = None
                if participant:
                    itinerary = participant.documents.filter(
                        doc_type='ACTUAL_ITINERARY', is_confirmed=True
                    ).first() or participant.documents.filter(doc_type='ITINERARY').first()
                amount = float(itinerary.extracted_amount) if itinerary and itinerary.extracted_amount else 0
                total += amount
                rows.append([
                    str(i),
                    travel.purpose[:60],
                    str(travel.start_date) if not travel.end_date else f"{travel.start_date} - {travel.end_date}",
                    travel.destination,
                    f'{amount:,.2f}',
                ])
            else:
                rows.append([
                    str(i),
                    travel.purpose[:60],
                    str(travel.start_date) if not travel.end_date else f"{travel.start_date} - {travel.end_date}",
                    travel.destination,
                ])

        t = make_travel_table(rows, show_total=(show_amounts and user.role == 'EMPLOYEE'), total=total)
        story.append(t)
        story.append(Spacer(1, 0.6*cm))

    # Signature
    story.append(Spacer(1, 0.5*cm))
    sig_data = [
        ['Prepared by:'],
        [''],
        [''],
        ['_________________________'],
        [user.get_full_name()],
        [user.get_role_display() if hasattr(user, 'get_role_display') else user.role],
    ]
    sig_table = Table(sig_data, colWidths=[7*cm])
    sig_table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), 'Helvetica'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('ALIGN',    (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (0,0),   'Helvetica-Bold'),
    ]))
    story.append(sig_table)

    doc.build(story)
    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="travel_records.pdf"'
    return response

# ══════════════════════════════════════════════════════════════════════
# BUDGET REPORT VIEW
# ══════════════════════════════════════════════════════════════════════
@never_cache
def budget_report_view(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role == 'EMPLOYEE':
        return redirect('travel_app:reports')

    from django.db.models import Count
    from django.core.paginator import Paginator

    today = timezone.now().date()
    year  = int(request.GET.get('year', today.year))
    bud_month     = int(request.GET.get('month', today.month))
    bud_source_id = request.GET.get('budget_source', 'all')
    page_num      = request.GET.get('page', 1)

    # Scope travels by role
    if user.role == 'DEPT_SEC':
        travels = TravelRecord.objects.filter(
            participants__college_name=user.college.name if user.college else ''
        ).distinct()
    elif user.role == 'CAMPUS_SEC':
        travels = TravelRecord.objects.filter(
            participants__campus_name=user.campus.name if user.campus else ''
        ).distinct()
    else:
        travels = TravelRecord.objects.all()

    # Available sources
    if user.role == 'DEPT_SEC':
        available_sources = BudgetSource.objects.filter(fiscal_year=year, budget_scope='COLLEGE', college=user.college)
    elif user.role == 'CAMPUS_SEC':
        available_sources = BudgetSource.objects.filter(fiscal_year=year, budget_scope='CAMPUS')
    else:
        available_sources = BudgetSource.objects.filter(fiscal_year=year)

    src_qs = available_sources
    if bud_source_id != 'all':
        src_qs = src_qs.filter(id=bud_source_id)

    MONTH_NAMES = ['','January','February','March','April','May','June',
                   'July','August','September','October','November','December']
    months = [{'num': i+1, 'name': MONTH_NAMES[i+1]} for i in range(12)]

    available_years = sorted(
        TravelRecord.objects.dates('start_date', 'year', order='DESC')
        .values_list('start_date__year', flat=True).distinct(),
        reverse=True
    ) or [today.year]

    budget_blocks = []
    for source in src_qs:
        src_travels = travels.filter(
            budget_source=source,
            start_date__year=year,
            start_date__month=bud_month,
        )
        rows = []
        total = 0
        for t in src_travels:
            for p in t.participants.filter(user__isnull=False):
                itinerary = p.documents.filter(
                    doc_type='ACTUAL_ITINERARY', is_confirmed=True
                ).first() or p.documents.filter(
                    doc_type='ITINERARY', extracted_amount__isnull=False
                ).first()
                amount = float(itinerary.extracted_amount) if itinerary and itinerary.extracted_amount else 0
                total += amount
                rows.append({
                    'name':        p.get_display_name(),
                    'dates':       f"{t.start_date}" if not t.end_date else f"{t.start_date} – {t.end_date}",
                    'destination': t.destination,
                    'purpose':     t.purpose[:60],
                    'amount':      f'{amount:,.2f}',
                })

        # Paginate rows
        paginator = Paginator(rows, 10)
        try:
            page_rows = paginator.page(page_num)
        except Exception:
            page_rows = paginator.page(1)

        budget_blocks.append({
            'name':      source.budget_name,
            'budget':    float(source.budget_amount),
            'total':     total,
            'balance':   float(source.budget_amount) - total,
            'rows':      page_rows,
            'paginator': paginator,
            'source_id': source.id,
        })

    context = {
        'user':              user,
        'today':             today,
        'selected_year':     year,
        'available_years':   available_years,
        'current_month':     today.month,
        'months':            months,
        'available_sources': available_sources,
        'budget_blocks':     budget_blocks,
        'bud_month':         bud_month,
        'bud_source_id':     bud_source_id,
    }
    return render(request, 'travel_app/shared/budget_report.html', context)