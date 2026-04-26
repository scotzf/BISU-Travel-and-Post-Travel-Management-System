# travel_app/budget_service.py

from decimal import Decimal
from django.db import transaction
from .models import BudgetSource, BudgetUsage


def get_budget_status(budget_source, user=None):
    """
    Return a summary dict for displaying in dashboards / dropdowns.
    """
    if user:
        try:
            usage = BudgetUsage.objects.get(
                user=user, budget_source=budget_source, year=budget_source.fiscal_year
            )
            pct = usage.usage_percentage
            return {
                'allocated':  budget_source.budget_amount,
                'used':       usage.used_amount,
                'remaining':  budget_source.budget_amount - usage.used_amount,
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
        usages     = BudgetUsage.objects.filter(budget_source=budget_source, year=budget_source.fiscal_year)
        total_used = sum(u.used_amount for u in usages)
        # Always use source.budget_amount as total — never sum usage rows
        total_alloc = budget_source.budget_amount
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

    DEPT_SEC   → COLLEGE-scoped sources
    CAMPUS_SEC → CAMPUS-scoped sources
    """
    from django.utils import timezone
    if year is None:
        year = timezone.now().year

    if user.role == 'DEPT_SEC':
        sources = BudgetSource.objects.filter(budget_scope='COLLEGE', fiscal_year=year, is_active=True)
        result  = []
        for s in sources:
            usages     = BudgetUsage.objects.filter(budget_source=s, year=year, user__college=user.college)
            total_used = sum(u.used_amount for u in usages)
            # Always use source.budget_amount — never sum allocated_amount from rows
            total_alloc = s.budget_amount
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
        sources = BudgetSource.objects.filter(budget_scope='CAMPUS', fiscal_year=year, is_active=True)
        result  = []
        for s in sources:
            usages     = BudgetUsage.objects.filter(budget_source=s, year=year, user__campus=user.campus)
            total_used = sum(u.used_amount for u in usages)
            # Always use source.budget_amount — never sum allocated_amount from rows
            total_alloc = s.budget_amount
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