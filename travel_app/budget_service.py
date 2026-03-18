# travel_app/budget_service.py
# Drop this file into your travel_app/ directory.
# Import and call these functions from your views — keeps budget logic
# out of views and makes it easy to unit-test independently.

from decimal import Decimal
from django.db import transaction
from .models import BudgetSource, BudgetUsage, CampusBudgetUsage


# ---------------------------------------------------------------------------
# Design decisions (change constants here if requirements change):
#
#   DEDUCT_ON_STATUS  — Which TravelOrder status triggers the budget deduction.
#                       'APPROVED' means money is reserved only after the
#                       Director approves, keeping the budget free during the
#                       approval pipeline.  Change to 'PENDING_DEAN' if you
#                       want to reserve on creation instead.
#
#   RESTORE_ON_STATUS — Which statuses should return the budget (cancellation /
#                       rejection).
#
#   OVER_BUDGET_POLICY— 'WARN'  → deduct anyway, return warning flag to view
#                        'BLOCK' → raise InsufficientBudgetError before saving
# ---------------------------------------------------------------------------

DEDUCT_ON_STATUS   = 'APPROVED'
RESTORE_ON_STATUS  = {'REJECTED'}
OVER_BUDGET_POLICY = 'WARN'   # change to 'BLOCK' to enforce hard limits


class InsufficientBudgetError(Exception):
    """Raised when OVER_BUDGET_POLICY == 'BLOCK' and funds are insufficient."""
    pass


def _get_amount(official_travel):
    """Return the estimated total for one travel (per-person × group size)."""
    itinerary = official_travel.itinerary
    count     = official_travel.participants_group.users.count() or 1
    return Decimal(str(itinerary.estimated_total)) * count


@transaction.atomic
def deduct_budget(official_travel):
    """
    Deduct this travel's estimated cost from its assigned BudgetSource.

    Returns a dict:
        {
            'success':    bool,
            'within_budget': bool,   # False means over-budget warning was issued
            'amount':     Decimal,
            'remaining':  Decimal,
            'message':    str,
        }

    Raises InsufficientBudgetError if OVER_BUDGET_POLICY == 'BLOCK' and
    funds are insufficient.
    """
    source = official_travel.budget_source
    if source is None:
        return {
            'success': True,
            'within_budget': True,
            'amount': Decimal('0'),
            'remaining': Decimal('0'),
            'message': 'No budget source assigned — skipped.',
        }

    amount = _get_amount(official_travel)

    if source.scope == 'COLLEGE':
        college = official_travel.travel_order.created_by.college
        if college is None:
            return {
                'success': False,
                'within_budget': False,
                'amount': amount,
                'remaining': Decimal('0'),
                'message': 'Travel creator has no college assigned.',
            }
        usage, _ = source.get_or_create_usage_for_college(college)

    else:  # CAMPUS
        campus = official_travel.travel_order.created_by.campus
        if campus is None:
            return {
                'success': False,
                'within_budget': False,
                'amount': amount,
                'remaining': Decimal('0'),
                'message': 'Travel creator has no campus assigned.',
            }
        usage, _ = source.get_or_create_campus_usage(campus)

    # Check budget before deducting
    if usage.remaining_amount < amount:
        if OVER_BUDGET_POLICY == 'BLOCK':
            raise InsufficientBudgetError(
                f"Insufficient budget in '{source.name}'. "
                f"Available: ₱{usage.remaining_amount:,.2f}, "
                f"Required: ₱{amount:,.2f}"
            )
        # WARN policy — proceed but flag it
        within_budget = False
    else:
        within_budget = True

    within = usage.deduct(amount)  # also returns bool

    # Snapshot the deducted amount on the travel record itself
    official_travel.budget_amount_deducted = amount
    official_travel.save(update_fields=['budget_amount_deducted'])

    return {
        'success':       True,
        'within_budget': within_budget,
        'amount':        amount,
        'remaining':     usage.remaining_amount,
        'message':       (
            'Budget deducted successfully.'
            if within_budget
            else f"⚠ Over budget! ₱{abs(usage.remaining_amount):,.2f} exceeded."
        ),
    }


@transaction.atomic
def restore_budget(official_travel):
    """
    Return a previously deducted amount back to its BudgetSource.
    Call this when a travel is rejected or cancelled after APPROVED status.

    Returns a dict similar to deduct_budget().
    """
    source = official_travel.budget_source
    amount = official_travel.budget_amount_deducted

    if source is None or amount == 0:
        return {
            'success': True,
            'message': 'Nothing to restore.',
        }

    if source.scope == 'COLLEGE':
        college = official_travel.travel_order.created_by.college
        try:
            usage = BudgetUsage.objects.get(
                college=college, budget_source=source, year=source.year
            )
        except BudgetUsage.DoesNotExist:
            return {'success': False, 'message': 'Budget usage record not found.'}
    else:
        campus = official_travel.travel_order.created_by.campus
        try:
            usage = CampusBudgetUsage.objects.get(
                campus=campus, budget_source=source, year=source.year
            )
        except CampusBudgetUsage.DoesNotExist:
            return {'success': False, 'message': 'Budget usage record not found.'}

    usage.restore(amount)

    # Reset the snapshot
    official_travel.budget_amount_deducted = Decimal('0')
    official_travel.save(update_fields=['budget_amount_deducted'])

    return {
        'success': True,
        'amount':  amount,
        'message': f'₱{amount:,.2f} restored to budget.',
    }


def get_budget_status(budget_source, college=None, campus=None):
    """
    Return a summary dict for displaying in dashboards / dropdowns.

    Pass `college` for COLLEGE-scoped sources, `campus` for CAMPUS-scoped.
    """
    if budget_source.scope == 'COLLEGE' and college:
        try:
            usage = BudgetUsage.objects.get(
                college=college, budget_source=budget_source, year=budget_source.year
            )
        except BudgetUsage.DoesNotExist:
            return {
                'allocated': budget_source.college_budget_amount,
                'used':      Decimal('0'),
                'remaining': budget_source.college_budget_amount,
                'percentage': 0,
                'status':    'unused',
            }
    elif budget_source.scope == 'CAMPUS' and campus:
        try:
            usage = CampusBudgetUsage.objects.get(
                campus=campus, budget_source=budget_source, year=budget_source.year
            )
        except CampusBudgetUsage.DoesNotExist:
            return {
                'allocated': budget_source.campus_budget_amount,
                'used':      Decimal('0'),
                'remaining': budget_source.campus_budget_amount,
                'percentage': 0,
                'status':    'unused',
            }
    else:
        return {}

    pct = usage.usage_percentage
    if pct >= 100:
        status = 'exhausted'
    elif pct >= 80:
        status = 'critical'   # < 20% left
    elif pct >= 60:
        status = 'warning'    # < 40% left
    else:
        status = 'healthy'

    return {
        'allocated':  usage.allocated_amount,
        'used':       usage.used_amount,
        'remaining':  usage.remaining_amount,
        'percentage': pct,
        'status':     status,  # used for Bootstrap colour class in templates
    }


def get_sources_for_secretary(user, year=None):
    """
    Return queryset of active BudgetSource objects available to the given
    secretary, along with budget status for each.

    DEPT_SEC  → COLLEGE-scoped sources
    CAMPUS_SEC → CAMPUS-scoped sources
    """
    from django.utils import timezone
    if year is None:
        year = timezone.now().year

    if user.role == 'DEPT_SEC':
        sources = BudgetSource.objects.filter(scope='COLLEGE', year=year, is_active=True)
        result  = []
        for s in sources:
            status = get_budget_status(s, college=user.college)
            result.append({'source': s, **status})
        return result

    elif user.role == 'CAMPUS_SEC':
        sources = BudgetSource.objects.filter(scope='CAMPUS', year=year, is_active=True)
        result  = []
        for s in sources:
            status = get_budget_status(s, campus=user.campus)
            result.append({'source': s, **status})
        return result

    return []