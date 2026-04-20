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
            if len(alerts) >= 10:
                break
 
            # Same destination (case insensitive)
            if a.destination.lower() != b.destination.lower():
                continue
 
            # Same start date (exact)
            if a.start_date != b.start_date:
                continue
 
            # Same end date (exact)
            if a.end_date != b.end_date:
                continue
 
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
            total_allocated = source.budget_amount * usages.count() if usages.exists() else source.budget_amount
            total_used      = sum(u.used_amount for u in usages)
        else:
            usages          = CampusBudgetUsage.objects.filter(budget_source=source, year=year)
            total_allocated = source.budget_amoun
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
# CREATE TRAVEL  (updated)
# Now handles the pre-filled form submission after extraction.
# Also links the temp Travel Order file as a TravelDocument.
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

    # Build participant list based on role
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
        extra_traveler_names = request.POST.getlist('extra_travelers')
        matched_traveler_ids = request.POST.getlist('matched_travelers')
        unregistered_names   = request.POST.getlist('unregistered_travelers')

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

                # Apply manual scope override if provided (employee only)
                scope_override = request.POST.get('scope_override', '').strip()
                if scope_override in ['COLLEGE', 'CAMPUS']:
                    travel.scope = scope_override
                    travel.scope_overridden = True
                    travel.save(update_fields=['scope', 'scope_overridden'])

                # Add creator as participant
                if user.role == 'EMPLOYEE':
                    TravelParticipant.objects.create(travel_record=travel, user=user)
                elif request.POST.get('include_creator') == 'yes':
                    TravelParticipant.objects.create(travel_record=travel, user=user)

                # Add matched travelers from extraction (all roles)
                for pid in matched_traveler_ids:
                    try:
                        participant = User.objects.get(id=pid, is_active=True)
                        TravelParticipant.objects.get_or_create(
                            travel_record=travel, user=participant
                        )
                    except User.DoesNotExist:
                        pass

                # Add selected participants from picker (secretaries only)
                if user.role in ['DEPT_SEC', 'CAMPUS_SEC'] and participant_ids:
                    for pid in participant_ids:
                        try:
                            participant = User.objects.get(id=pid, is_active=True)
                            TravelParticipant.objects.get_or_create(
                                travel_record=travel, user=participant
                            )
                        except User.DoesNotExist:
                            pass

                # Save unregistered traveler names (not in system, can't be participants)
                if unregistered_names:
                    travel.unregistered_travelers = unregistered_names
                    travel.save(update_fields=['unregistered_travelers'])

                travel.refresh_scope()

                # ── Link the temp Travel Order file if extraction was done ──
                temp_path       = request.session.pop('temp_travel_order_path', None)
                extracted_names = request.session.pop('temp_travel_order_names', [])

                if temp_path:
                    try:
                        from django.core.files.storage import default_storage
                        from django.core.files import File

                        full_path = default_storage.path(temp_path)

                        # Merge extracted names + manually added names
                        all_traveler_names = extracted_names + extra_traveler_names

                        with open(full_path, 'rb') as f:
                            travel_doc = TravelDocument(
                                travel_record=travel,
                                doc_type='TRAVEL_ORDER',
                                uploaded_by=user,
                                extracted_destination=destination,
                                extracted_purpose=purpose,
                                extracted_traveler_names=all_traveler_names,
                                extraction_status='done',
                                extraction_successful=True,
                            )
                            if start_date:
                                from datetime import datetime
                                travel_doc.extracted_start_date = datetime.strptime(
                                    start_date, '%Y-%m-%d'
                                ).date()
                            if end_date:
                                from datetime import datetime
                                travel_doc.extracted_end_date = datetime.strptime(
                                    end_date, '%Y-%m-%d'
                                ).date()

                            import os
                            file_name = os.path.basename(temp_path)
                            travel_doc.file.save(file_name, File(f), save=True)

                        # Delete temp file
                        default_storage.delete(temp_path)

                    except Exception as e:
                        logger.error(f"Failed to link temp travel order: {e}")

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

        from django.contrib import messages
        messages.success(request, f'{doc.get_doc_type_display()} uploaded successfully.')

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
                    from decimal import Decimal, InvalidOperation
 
                    # Read amount from form
                    raw_amount = request.POST.get('amount', '').strip().replace(',', '')
                    try:
                        amount_deducted = Decimal(raw_amount) if raw_amount else Decimal('0')
                        if amount_deducted < 0:
                            amount_deducted = Decimal('0')
                    except InvalidOperation:
                        amount_deducted = Decimal('0')
 
                    travel.budget_source    = source
                    travel.budget_tagged_by = user
                    travel.budget_tagged_at = tz.now()
                    travel.amount_deducted  = amount_deducted
                    travel.save(update_fields=[
                        'budget_source', 'budget_tagged_by',
                        'budget_tagged_at', 'amount_deducted'
                    ])
 
                    # Create usage row and deduct amount
                    if source.scope == 'COLLEGE' and user.college:
                        usage, _ = source.get_or_create_college_usage(user.college)
                        if amount_deducted > 0:
                            usage.deduct(amount_deducted)
                    elif source.scope == 'CAMPUS' and user.campus:
                        usage, _ = source.get_or_create_campus_usage(user.campus)
                        if amount_deducted > 0:
                            usage.deduct(amount_deducted)
 
                    from django.contrib import messages
                    messages.success(request, f'Budget source "{source.name}" assigned with ₱{amount_deducted:,.2f} deducted.')
 
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
        travels = travels.filter(budget_source__isnull=False)  # .filter = where() sa sql query
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
            budget_amount = request.POST.get('budget_amount', 0) or 0
            description           = request.POST.get('description', '').strip()
            source_year           = int(request.POST.get('year', today.year))
            if not name:
                from django.contrib import messages
                messages.error(request, 'Budget source name is required.')
            else:
                try:
                    BudgetSource.objects.create(
                        name=name, scope=scope, year=source_year,
                        budget_amount=budget_amount,
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
            total_allocated = source.budget_amount * College.objects.count()
            total_used      = sum(u.used_amount for u in usages)
        else:
            usages          = CampusBudgetUsage.objects.filter(budget_source=source, year=year)
            total_allocated = source.budget_amount
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
                total_alloc = source.budget_amount * College.objects.count() 
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
                total_alloc = source.budget_amount
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

# ══════════════════════════════════════════════════════════════════════
# EVENT GROUPS — full feature
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

 
@csrf_protect
@never_cache
def create_event_group(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['ADMIN', 'DEPT_SEC', 'CAMPUS_SEC']:
        return redirect('accounts:dashboard')
 
    # Must come with ?a=ID&b=ID from duplicate alert
    a_id = request.GET.get('a') or request.POST.get('a')
    b_id = request.GET.get('b') or request.POST.get('b')
 
    if not a_id or not b_id:
        from django.contrib import messages
        messages.error(request, 'Please use the "Link as Event Group" button from a duplicate alert.')
        return redirect('travel_app:event_groups')
 
    try:
        travel_a = TravelRecord.objects.get(id=a_id)
        travel_b = TravelRecord.objects.get(id=b_id)
    except TravelRecord.DoesNotExist:
        from django.contrib import messages
        messages.error(request, 'One or both travels not found.')
        return redirect('travel_app:event_groups')
 
    # Suggested name
    suggested_name = f"{travel_a.destination} — {travel_a.start_date.strftime('%B %Y')}"
 
    if request.method == 'POST':
        name  = request.POST.get('name', '').strip()
        notes = request.POST.get('notes', '').strip()
 
        if not name:
            from django.contrib import messages
            messages.error(request, 'Event group name is required.')
            return render(request, 'travel_app/shared/create_event_group.html', {
                'user': user, 'travel_a': travel_a, 'travel_b': travel_b,
                'suggested_name': suggested_name, 'post': request.POST,
            })
 
        with transaction.atomic():
            travels  = [travel_a, travel_b]
            end_dates = [t.end_date for t in travels if t.end_date]
 
            group = EventGroup.objects.create(
                name        = name,
                destination = travel_a.destination,
                start_date  = travel_a.start_date,
                end_date    = max(end_dates) if end_dates else None,
                notes       = notes,
                created_by  = user,
                scope       = 'CAMPUS' if len(set(
                    p.college_snapshot
                    for t in travels
                    for p in t.participants.all()
                    if p.college_snapshot
                )) > 1 else 'COLLEGE',
            )
 
            travel_a.event_group = group
            travel_b.event_group = group
            travel_a.save(update_fields=['event_group'])
            travel_b.save(update_fields=['event_group'])
 
        from django.contrib import messages
        messages.success(request, f'Event group "{name}" created. Both travels are now linked.')
        return redirect('travel_app:event_groups')
    
    return render(request, 'travel_app/shared/create_event_group.html', {
        'user':           user,
        'travel_a':       travel_a,
        'travel_b':       travel_b,
        'suggested_name': suggested_name,
        'post':           {},
    })


@never_cache
def event_group_detail(request, pk):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['ADMIN', 'DEPT_SEC', 'CAMPUS_SEC']:
        return redirect('accounts:dashboard')
 
    group = get_object_or_404(
        EventGroup.objects.prefetch_related(
            'travel_records__participants__user',
            'travel_records__budget_source',
        ),
        pk=pk
    )
 
    context = {
        'user':    user,
        'group':   group,
        'travels': group.travel_records.all(),
        'today':   timezone.now().date(),
    }
    return render(request, 'travel_app/shared/event_group_detail.html', context)
 
 
@csrf_protect
@never_cache
def edit_event_group(request, pk):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['ADMIN', 'DEPT_SEC', 'CAMPUS_SEC']:
        return redirect('accounts:dashboard')
 
    group = get_object_or_404(EventGroup, pk=pk)
 
    if request.method == 'POST':
        name  = request.POST.get('name', '').strip()
        notes = request.POST.get('notes', '').strip()
 
        if not name:
            from django.contrib import messages
            messages.error(request, 'Name is required.')
            return redirect('travel_app:event_group_detail', pk=pk)
 
        group.name  = name
        group.notes = notes
        group.save(update_fields=['name', 'notes'])
 
        from django.contrib import messages
        messages.success(request, f'Event group renamed to "{name}".')
 
    return redirect('travel_app:event_group_detail', pk=pk)
 
@csrf_protect
@never_cache
def delete_event_group(request, pk):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['ADMIN', 'DEPT_SEC', 'CAMPUS_SEC']:
        return redirect('accounts:dashboard')
 
    group = get_object_or_404(EventGroup, pk=pk)
 
    if request.method == 'POST':
        name = group.name
        group.travel_records.all().update(event_group=None)
        group.delete()
 
        from django.contrib import messages
        messages.success(request, f'Event group "{name}" deleted. Travels have been unlinked.')
 
    return redirect('travel_app:event_groups')
 

@csrf_protect
@never_cache
def unlink_travel_from_group(request, pk, travel_pk):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role not in ['ADMIN', 'DEPT_SEC', 'CAMPUS_SEC']:
        return redirect('accounts:dashboard')
 
    group  = get_object_or_404(EventGroup, pk=pk)
    travel = get_object_or_404(TravelRecord, pk=travel_pk, event_group=group)
 
    if request.method == 'POST':
        travel.event_group = None
        travel.save(update_fields=['event_group'])
 
        from django.contrib import messages
        messages.success(request, f'"{travel.destination}" unlinked from {group.name}.')
 
    return redirect('travel_app:event_group_detail', pk=pk)
 
 
# ══════════════════════════════════════════════════════════════════════
# ADD TRAVEL TO GROUP (removed — duplicates only, no manual adding)
# Keep this stub so the URL doesn't 404 if old links exist
# ══════════════════════════════════════════════════════════════════════
 
@csrf_protect
@never_cache
def add_travel_to_group(request, pk):
    from django.contrib import messages
    messages.error(request, 'Travels can only be linked via duplicate detection.')
    return redirect('travel_app:event_group_detail', pk=pk)

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


# ══════════════════════════════════════════════════════════════════════
# REJECT EXTRACTION  (updated — removed old fields)
# ══════════════════════════════════════════════════════════════════════
 
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
 
    doc    = get_object_or_404(TravelDocument, id=doc_id)
    travel = doc.travel_record
 
    if request.method == 'POST':
        doc.extracted_destination    = ''
        doc.extracted_start_date     = None
        doc.extracted_end_date       = None
        doc.extracted_amount         = None
        doc.extracted_purpose        = ''
        doc.extracted_traveler_names = []
        doc.extraction_successful    = False
        doc.extraction_status        = 'failed'
        doc.save(update_fields=[
            'extracted_destination', 'extracted_start_date', 'extracted_end_date',
            'extracted_amount', 'extracted_purpose', 'extracted_traveler_names',
            'extraction_successful', 'extraction_status',
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

    # ── Scope filter ──────────────────────────────────────────────────
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

    # ── Year filter ───────────────────────────────────────────────────
    selected_year = int(request.GET.get('year', this_year))
    travels_year  = travels.filter(start_date__year=selected_year)

    year_list = sorted(set(
        TravelRecord.objects.filter(
            id__in=travels.values_list('id', flat=True)
        ).dates('start_date', 'year').values_list('start_date__year', flat=True)
    ), reverse=True)
    if not year_list:
        year_list = [this_year]

    # ── Event group deduplication ─────────────────────────────────────
    # For counting purposes, travels in the same event group count as ONE.
    # We keep one representative per group (the earliest created_at),
    # plus all ungrouped travels.
    #
    # deduplicated_travels_year = the queryset used for counts/charts
    # so stats don't double-count grouped travels.

    grouped_travel_ids = set()   # IDs to exclude (non-representative duplicates)
    seen_groups        = set()

    for travel in travels_year.select_related('event_group').order_by('created_at'):
        if travel.event_group_id:
            if travel.event_group_id in seen_groups:
                # Already have a representative for this group — exclude this one
                grouped_travel_ids.add(travel.id)
            else:
                seen_groups.add(travel.event_group_id)
                # This is the representative — keep it

    # Deduplicated queryset: ungrouped + one representative per group
    deduped = travels_year.exclude(id__in=grouped_travel_ids)

    # ══════════════════════════════════════════════════════════════════
    # SECTION 1 — TRAVEL VOLUME
    # ══════════════════════════════════════════════════════════════════

    # Travels per month (deduplicated)
    monthly_raw = (
        deduped
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

    # Travels by scope (deduplicated)
    scope_data = {
        'COLLEGE': deduped.filter(scope='COLLEGE').count(),
        'CAMPUS':  deduped.filter(scope='CAMPUS').count(),
    }

    # Travels per college (deduplicated)
    college_volume = (
        deduped
        .values('participants__college_snapshot')
        .annotate(count=Count('id', distinct=True))
        .exclude(participants__college_snapshot='')
        .order_by('-count')[:8]
    )
    college_vol_labels = [r['participants__college_snapshot'] or 'Unknown' for r in college_volume]
    college_vol_data   = [r['count'] for r in college_volume]

    # Yearly totals (deduplicated, all years)
    # Build deduped set for all years too
    all_grouped_ids = set()
    all_seen_groups = set()
    for travel in travels.select_related('event_group').order_by('created_at'):
        if travel.event_group_id:
            if travel.event_group_id in all_seen_groups:
                all_grouped_ids.add(travel.id)
            else:
                all_seen_groups.add(travel.event_group_id)

    all_deduped = travels.exclude(id__in=all_grouped_ids)
    yearly_raw = (
        all_deduped
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
        budget_usages = BudgetUsage.objects.filter(year=selected_year).select_related('budget_source', 'college')
        campus_usages = CampusBudgetUsage.objects.filter(year=selected_year).select_related('budget_source', 'campus')
    elif user.role == 'CAMPUS_SEC':
        budget_usages = BudgetUsage.objects.none()
        campus_usages = CampusBudgetUsage.objects.filter(year=selected_year, campus=user.campus).select_related('budget_source')
    else:  # DEPT_SEC
        budget_usages = BudgetUsage.objects.filter(year=selected_year, college=user.college).select_related('budget_source')
        campus_usages = CampusBudgetUsage.objects.none()

    # Monthly spend — use ALL travels (not deduped) since each travel
    # has its own real budget deduction even if grouped
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

    total_allocated = sum(u.allocated_amount for u in budget_usages) + \
                      sum(u.allocated_amount for u in campus_usages)
    total_used      = sum(u.used_amount for u in budget_usages) + \
                      sum(u.used_amount for u in campus_usages)
    total_remaining = total_allocated - total_used

    # ══════════════════════════════════════════════════════════════════
    # SECTION 3 — PARTICIPANT STATS
    # ══════════════════════════════════════════════════════════════════

    from .models import TravelParticipant

    # Top 8 most frequent travelers — use ALL travels (real participants)
    top_travelers = (
        TravelParticipant.objects
        .filter(travel_record__in=travels_year)
        .values('user__first_name', 'user__last_name', 'college_snapshot')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )

    # Average participants per event (deduplicated)
    # For grouped events, sum participants across all travels in the group
    avg_participants = deduped.annotate(
        pcount=Count('participants')
    ).aggregate(avg=Avg('pcount'))['avg'] or 0

    # Total participant-days (all travels — each person's days count)
    total_participant_days = 0
    for t in travels_year.annotate(pcount=Count('participants')):
        total_participant_days += t.pcount * t.get_duration_days()

    # Travels per college (participant perspective — all travels)
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
            'type':    'warning',
            'icon':    'bi-folder-x',
            'title':   f'{no_docs.count()} travel(s) with no documents uploaded',
            'detail':  ', '.join([str(t.destination) for t in no_docs[:3]]) +
                       ('...' if no_docs.count() > 3 else ''),
            'travels': list(no_docs.values('id', 'destination', 'start_date')[:5]),
        })

    # 2. Budget sources at ≥80% usage
    critical_budgets = [u for u in list(budget_usages) + list(campus_usages)
                        if u.usage_percentage >= 80]
    for u in critical_budgets:
        label = u.college.name if hasattr(u, 'college') else u.campus.name
        anomalies.append({
            'type':    'critical' if u.usage_percentage >= 100 else 'warning',
            'icon':    'bi-exclamation-triangle-fill',
            'title':   f'{u.budget_source.name} — {label} at {u.usage_percentage}%',
            'detail':  f'₱{u.used_amount:,.0f} used of ₱{u.allocated_amount:,.0f}',
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

    # 4. Unlinked duplicates (same destination + dates, not yet grouped)
    ungrouped_this_year = travels_year.filter(event_group__isnull=True)
    duplicate_pairs     = _detect_duplicates(ungrouped_this_year)
    if duplicate_pairs:
        anomalies.append({
            'type':    'info',
            'icon':    'bi-copy',
            'title':   f'{len(duplicate_pairs)} possible duplicate travel(s) detected',
            'detail':  'Same destination and dates — consider linking as event group',
            'travels': [],
        })

    # ── Summary cards ─────────────────────────────────────────────────
    total_travels    = deduped.count()  # deduplicated event count
    total_this_month = deduped.filter(start_date__month=this_month).count()
    out_of_province  = deduped.filter(is_out_of_province=True).count()

    context = {
        'user':          user,
        'is_admin':      user.role == 'ADMIN',
        'is_secretary':  user.role in ['CAMPUS_SEC', 'DEPT_SEC'],

        # Year filter
        'selected_year': selected_year,
        'year_list':     year_list,
        'this_year':     this_year,

        # Summary
        'total_travels':          total_travels,
        'total_this_month':       total_this_month,
        'out_of_province':        out_of_province,
        'avg_participants':       round(avg_participants, 1),
        'total_participant_days': total_participant_days,
        'total_allocated':        total_allocated,
        'total_used':             total_used,
        'total_remaining':        total_remaining,

        # Charts
        'monthly_labels':       json_module.dumps(monthly_labels),
        'monthly_data':         json_module.dumps(monthly_data),
        'yearly_labels':        json_module.dumps(yearly_labels),
        'yearly_data':          json_module.dumps(yearly_data),
        'scope_data':           json_module.dumps(scope_data),
        'college_vol_labels':   json_module.dumps(college_vol_labels),
        'college_vol_data':     json_module.dumps(college_vol_data),
        'monthly_spend_data':   json_module.dumps(monthly_spend_data),
        'monthly_labels_spend': json_module.dumps(monthly_labels),

        # Tables
        'budget_usages':         budget_usages,
        'campus_usages':         campus_usages,
        'top_travelers':         top_travelers,
        'college_participation': college_participation,

        # Anomalies
        'anomalies':     anomalies,
        'anomaly_count': len(anomalies),
    }

    return render(request, 'travel_app/shared/stats.html', context)
# ══════════════════════════════════════════════════════════════════════
# EXTRACT TRAVEL ORDER (AJAX)
# Called when user uploads a Travel Order on the create travel page.
# Returns extracted data as JSON for pre-filling the form.
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
 
        # Save file temporarily
        temp_path = default_storage.save(
            f'travel_documents/temp/{file.name}',
            ContentFile(file.read())
        )
        full_path = default_storage.path(temp_path)
 
        # Extract text + run Ollama
        text, method = extract_text_from_file(full_path)
 
        if not text or len(text.strip()) < 20:
            default_storage.delete(temp_path)
            return JsonResponse({
                'success': False,
                'error': 'Could not extract text from this file. Try a clearer scan or different format.'
            })
 
        result = _extract_travel_order(text)
 
        # ── Match traveler names against DB (fast — one query per name) ──
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
                    match = User.objects.filter(
                        query, is_active=True, is_approved=True
                    ).first()
 
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
 
        # Store temp path and extracted names in session
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
    

@csrf_protect
@never_cache
def change_scope(request, pk):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    travel = get_object_or_404(TravelRecord, pk=pk)

    # Only secretary or participants/creator can change scope
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
            travel.scope            = new_scope
            travel.scope_overridden = True
            travel.save(update_fields=['scope', 'scope_overridden'])

            from django.contrib import messages
            messages.success(request, f'Travel scope changed to {travel.get_scope_display()}.')
        else:
            from django.contrib import messages
            messages.error(request, 'Invalid scope.')

    return redirect('travel_app:travel_detail', pk=pk)

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
            'found':    True,
            'id':       match.id,
            'name':     match.get_full_name(),
            'college':  match.college.name if match.college else '',
        })
    return JsonResponse({'found': False})

@csrf_protect
@never_cache
def set_document_amount(request, doc_id):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
 
    doc    = get_object_or_404(TravelDocument, id=doc_id)
    travel = doc.travel_record
 
    is_participant = travel.participants.filter(user=user).exists()
    is_secretary   = user.role in ['DEPT_SEC', 'CAMPUS_SEC']
    is_admin       = user.role == 'ADMIN'
 
    if not (is_participant or is_secretary or is_admin):
        from django.contrib import messages
        messages.error(request, 'You do not have permission to set this amount.')
        return redirect('travel_app:travel_detail', pk=travel.id)
 
    if doc.doc_type not in ('BURS', 'ITINERARY'):
        from django.contrib import messages
        messages.error(request, 'Amount can only be set on BURS or Itinerary documents.')
        return redirect('travel_app:travel_detail', pk=travel.id)
 
    if request.method == 'POST':
        from decimal import Decimal, InvalidOperation
        raw = request.POST.get('amount', '').strip().replace(',', '')
        try:
            amount = Decimal(raw)
            if amount < 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            from django.contrib import messages
            messages.error(request, 'Invalid amount. Please enter a valid number.')
            return redirect('travel_app:travel_detail', pk=travel.id)
 
        doc.extracted_amount = amount
        doc.save(update_fields=['extracted_amount'])
 
        from django.contrib import messages
        messages.success(request, f'Amount of ₱{amount:,.2f} saved successfully.')
 
    return redirect('travel_app:travel_detail', pk=travel.id)

@csrf_protect
@never_cache
def replace_document(request, doc_id):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    doc    = get_object_or_404(TravelDocument, id=doc_id)
    travel = doc.travel_record

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

    if request.method == 'POST':
        file = request.FILES.get('file')
        if not file:
            from django.contrib import messages
            messages.error(request, 'No file provided.')
            return redirect('travel_app:travel_detail', pk=travel.id)

        doc.file        = file
        doc.uploaded_by = user
        doc.uploaded_at = timezone.now()
        doc.save(update_fields=['file', 'uploaded_by', 'uploaded_at'])

        from django.contrib import messages
        messages.success(request, f'{doc.get_doc_type_display()} replaced successfully.')

    return redirect('travel_app:travel_detail', pk=travel.id)

@never_cache
def liquidation_calculator(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    # Scope travels based on role
    if user.role == 'EMPLOYEE':
        travels = TravelRecord.objects.filter(
            participants__user=user
        ).distinct()
    elif user.role == 'DEPT_SEC':
        travels = TravelRecord.objects.filter(
            scope='COLLEGE',
            participants__college_snapshot=user.college.name if user.college else ''
        ).distinct()
    elif user.role == 'CAMPUS_SEC':
        travels = TravelRecord.objects.filter(
            participants__campus_snapshot=user.campus.name if user.campus else ''
        ).distinct()
    else:  # ADMIN
        travels = TravelRecord.objects.all()

    travels = travels.filter(
        budget_source__isnull=False
    ).order_by('-start_date')

    # Only show travels that have a budget tagged
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
        'user':           user,
        'travels':        travels,
        'selected_travel': selected_travel,
        'selected_id':    selected_id,
        'actual_amount':  actual_amount,
        'result':         result,
        'today':          timezone.now().date(),
    }
    return render(request, 'travel_app/shared/liquidation_calculator.html', context)