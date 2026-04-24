# travel_app/budget_service.py

from decimal import Decimal
from django.db import transaction
from .models import BudgetSource, BudgetUsage


def get_budget_status(budget_source, user=None):
    """
    Return a summary dict for displaying in dashboards / dropdowns.
    Uses the new per-user BudgetUsage model.
    If user is None, returns totals aggregated across all users of that source.
    """
    if user:
        try:
            usage = BudgetUsage.objects.get(
                user=user, budget_source=budget_source, year=budget_source.year
            )
            pct = usage.usage_percentage
            return {
                'allocated':  usage.allocated_amount,
                'used':       usage.used_amount,
                'remaining':  usage.remaining_amount,
                'percentage': pct,
                'status':     _status_label(pct),
            }
        except BudgetUsage.DoesNotExist:
            return {
                'allocated':  budget_source.budget_amount,
                'used':       Decimal('0'),
                'remaining':  budget_source.budget_amount,
                'percentage': 0,
                'status':     'unused',
            }
    else:
        # Aggregate across all users for this source
        usages      = BudgetUsage.objects.filter(budget_source=budget_source, year=budget_source.year)
        total_used  = sum(u.used_amount for u in usages)
        total_alloc = sum(u.allocated_amount for u in usages) or budget_source.budget_amount
        pct         = round((total_used / total_alloc * 100), 1) if total_alloc > 0 else 0
        return {
            'allocated':  total_alloc,
            'used':       total_used,
            'remaining':  total_alloc - total_used,
            'percentage': pct,
            'status':     _status_label(pct),
        }


def _status_label(pct):
    if pct >= 100: return 'exhausted'
    if pct >= 80:  return 'critical'
    if pct >= 60:  return 'warning'
    return 'healthy'


def get_sources_for_secretary(user, year=None):
    """
    Return list of active BudgetSource objects available to the given
    secretary, along with aggregated budget status for each.

    DEPT_SEC   → COLLEGE-scoped sources, aggregated across their college users
    CAMPUS_SEC → CAMPUS-scoped sources, aggregated across their campus users
    """
    from django.utils import timezone
    if year is None:
        year = timezone.now().year

    if user.role == 'DEPT_SEC':
        sources = BudgetSource.objects.filter(scope='COLLEGE', year=year, is_active=True)
        result  = []
        for s in sources:
            # Aggregate usage for all users in this secretary's college
            usages      = BudgetUsage.objects.filter(
                budget_source=s, year=year,
                user__college=user.college
            )
            total_used  = sum(u.used_amount for u in usages)
            total_alloc = sum(u.allocated_amount for u in usages) or s.budget_amount
            pct         = round((total_used / total_alloc * 100), 1) if total_alloc > 0 else 0
            result.append({
                'source':     s,
                'allocated':  total_alloc,
                'used':       total_used,
                'remaining':  total_alloc - total_used,
                'percentage': pct,
                'status':     _status_label(pct),
            })
        return result

    elif user.role == 'CAMPUS_SEC':
        sources = BudgetSource.objects.filter(scope='CAMPUS', year=year, is_active=True)
        result  = []
        for s in sources:
            # Aggregate usage for all users in this secretary's campus
            usages      = BudgetUsage.objects.filter(
                budget_source=s, year=year,
                user__campus=user.campus
            )
            total_used  = sum(u.used_amount for u in usages)
            total_alloc = sum(u.allocated_amount for u in usages) or s.budget_amount
            pct         = round((total_used / total_alloc * 100), 1) if total_alloc > 0 else 0
            result.append({
                'source':     s,
                'allocated':  total_alloc,
                'used':       total_used,
                'remaining':  total_alloc - total_used,
                'percentage': pct,
                'status':     _status_label(pct),
            })
        return result

    return []