from django.shortcuts import render, redirect
from django.views.decorators.cache import never_cache
from django.utils import timezone
from accounts.views import get_authenticated_user
from .models import (
    TravelRecord, TravelDocument, BudgetSource,
    BudgetUsage, CampusBudgetUsage
)
from .budget_service import get_sources_for_secretary


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def require_role(roles):
    """
    Decorator factory — redirects if user's role is not in roles list.
    Usage:  @require_role(['EMPLOYEE'])
    """
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
    """Return a dict of common stats for a travel queryset."""
    today = timezone.now().date()
    return {
        'total_travels':     qs.count(),
        'completed_travels': qs.filter(end_date__lt=today).count(),
        'upcoming_travels':  qs.filter(start_date__gt=today).count(),
        'incomplete_travels': sum(1 for t in qs if t.completeness_percentage < 100),
    }


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
        'user':             user,
        'today':            timezone.now().date(),
        'recent_travels':   my_travels[:6],
        'doc_types':        TravelDocument.DOC_TYPE_CHOICES,
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

    # All travels from this secretary's college
    college_travels = TravelRecord.objects.filter(
        scope='COLLEGE',
        participants__college_snapshot=user.college.name if user.college else ''
    ).select_related('created_by', 'budget_source').prefetch_related('participants').distinct()

    untagged = college_travels.filter(budget_source__isnull=True)

    # Budget sources for this secretary's college
    budget_sources = get_sources_for_secretary(user, year=year)

    # Total amount used across all sources
    total_budget_used = sum(item.get('used', 0) for item in budget_sources)

    # Duplicate detection — travels with same destination and overlapping dates
    duplicate_alerts = _detect_duplicates(college_travels)

    context = {
        'user':             user,
        'today':            today,
        'current_year':     year,
        'total_travels':    college_travels.count(),
        'untagged_count':   untagged.count(),
        'untagged_travels': untagged[:8],
        'recent_travels':   college_travels[:8],
        'total_travelers':  sum(t.participant_count for t in college_travels),
        'total_budget_used': total_budget_used,
        'budget_sources':   budget_sources,
        'duplicate_alerts': duplicate_alerts,
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

    # All campus-scope travels
    campus_travels = TravelRecord.objects.filter(
        scope='CAMPUS',
        participants__campus_snapshot=user.campus.name if user.campus else ''
    ).select_related('created_by', 'budget_source').prefetch_related('participants').distinct()

    untagged = campus_travels.filter(budget_source__isnull=True)

    budget_sources = get_sources_for_secretary(user, year=year)
    total_budget_used = sum(item.get('used', 0) for item in budget_sources)
    duplicate_alerts  = _detect_duplicates(campus_travels)

    context = {
        'user':             user,
        'today':            today,
        'current_year':     year,
        'total_travels':    campus_travels.count(),
        'untagged_count':   untagged.count(),
        'untagged_travels': untagged[:8],
        'recent_travels':   campus_travels[:8],
        'total_travelers':  sum(t.participant_count for t in campus_travels),
        'total_budget_used': total_budget_used,
        'budget_sources':   budget_sources,
        'duplicate_alerts': duplicate_alerts,
    }
    return render(request, 'travel_app/secretary/dashboard.html', context)


# ══════════════════════════════════════════════════════════════════════
# ADMIN DASHBOARD
# ══════════════════════════════════════════════════════════════════════

@never_cache
@require_role(['ADMIN'])
def admin_dashboard(request, user=None):
    from django.db.models import Count
    from accounts.models import College

    today = timezone.now().date()
    year  = today.year

    all_travels = TravelRecord.objects.select_related(
        'created_by__college', 'budget_source'
    ).prefetch_related('participants').all()

    # Budget sources — aggregate usage across all colleges
    sources     = BudgetSource.objects.filter(year=year, is_active=True)
    budget_data = []
    for source in sources:
        if source.scope == 'COLLEGE':
            usages = BudgetUsage.objects.filter(budget_source=source, year=year)
            total_allocated = source.college_budget_amount * usages.count() if usages.exists() else source.college_budget_amount
            total_used      = sum(u.used_amount for u in usages)
        else:
            usages = CampusBudgetUsage.objects.filter(budget_source=source, year=year)
            total_allocated = source.campus_budget_amount
            total_used      = sum(u.used_amount for u in usages)

        pct = round((total_used / total_allocated * 100), 1) if total_allocated > 0 else 0
        status = 'exhausted' if pct >= 100 else 'critical' if pct >= 80 else 'warning' if pct >= 60 else 'healthy'
        budget_data.append({
            'source':     source,
            'allocated':  total_allocated,
            'used':       total_used,
            'remaining':  total_allocated - total_used,
            'percentage': pct,
            'status':     status,
        })

    # Travels by college for chart
    college_stats = []
    for college in College.objects.all():
        count = all_travels.filter(
            participants__college_snapshot=college.name
        ).distinct().count()
        if count > 0:
            college_stats.append({'college': college.code or college.name[:10], 'count': count})

    context = {
        'user':           user,
        'today':          today,
        'current_year':   year,
        'total_travels':  all_travels.count(),
        'untagged_count': all_travels.filter(budget_source__isnull=True).count(),
        'total_travelers': sum(t.participant_count for t in all_travels),
        'total_colleges':  College.objects.count(),
        'budget_sources':  budget_data,
        'college_stats':   college_stats,
        'recent_travels':  all_travels[:8],
    }
    return render(request, 'travel_app/admin/dashboard.html', context)


# ══════════════════════════════════════════════════════════════════════
# SHARED PLACEHOLDER VIEWS
# (will be replaced in Phase 3 & 4)
# ══════════════════════════════════════════════════════════════════════

@never_cache
def my_travels(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    return redirect('travel_app:employee_dashboard')   # placeholder


@never_cache
def my_stats(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    return redirect('travel_app:employee_dashboard')   # placeholder


@never_cache
def create_travel(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    return redirect('accounts:dashboard')              # placeholder


@never_cache
def travel_detail(request, pk):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    return redirect('accounts:dashboard')              # placeholder


@never_cache
def all_travels(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    return redirect('accounts:dashboard')              # placeholder


@never_cache
def budget_overview(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    return redirect('accounts:dashboard')              # placeholder


@never_cache
def manage_budget_sources(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    return redirect('accounts:dashboard')              # placeholder


@never_cache
def event_groups(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    return redirect('accounts:dashboard')              # placeholder


# ══════════════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ══════════════════════════════════════════════════════════════════════

def _detect_duplicates(travels_qs):
    """
    Simple duplicate detector — finds pairs of travels with the same
    destination and overlapping dates that are NOT already in an event group.
    Returns a list of (travel_a, travel_b) tuples, capped at 5.
    """
    travels = list(travels_qs.filter(event_group__isnull=True).order_by('destination', 'start_date'))
    alerts  = []
    seen    = set()

    for i, a in enumerate(travels):
        for b in travels[i+1:]:
            if len(alerts) >= 5:
                break
            if a.destination.lower() != b.destination.lower():
                continue
            # Check date overlap
            a_end = a.end_date or a.start_date
            b_end = b.end_date or b.start_date
            if a.start_date <= b_end and b.start_date <= a_end:
                key = tuple(sorted([a.id, b.id]))
                if key not in seen:
                    seen.add(key)
                    alerts.append((a, b))

    return alerts