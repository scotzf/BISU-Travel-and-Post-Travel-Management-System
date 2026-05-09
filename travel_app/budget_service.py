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
        sources = BudgetSource.objects.filter(budget_scope='COLLEGE', fiscal_year=year, is_active=True, college=user.college)
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

def liquidate_participant(participant, actual_amount):
    from .models import ParticipantDocument, BudgetUsage
    from decimal import Decimal

    actual_amount = Decimal(str(actual_amount))

    # Get the original itinerary (planned amount)
    original_doc = ParticipantDocument.objects.filter(
        participant=participant,
        doc_type='ITINERARY',
        extracted_amount__isnull=False
    ).order_by('-uploaded_at').first()

    if not original_doc:
        return {
            'success': False,
            'reason': 'no_itinerary',
            'message': 'No original itinerary amount found. Cannot liquidate.',
        }

    travel = participant.travel_record
    if not travel.budget_source:
        return {
            'success': False,
            'reason': 'no_budget_tagged',
            'message': 'No budget source tagged for this travel.',
        }

    source = travel.budget_source

    try:
        usage = BudgetUsage.objects.get(
            user=participant.user,
            budget_source=source,
            year=source.fiscal_year,
        )
    except BudgetUsage.DoesNotExist:
        return {
            'success': False,
            'reason': 'no_usage_record',
            'message': 'No budget usage record found for this participant.',
        }

    original_amount = Decimal(str(original_doc.extracted_amount))

    # Check if a previous liquidation was already applied.
    # We track what was previously applied via the usage.used_amount vs original_amount.
    # The current used_amount already reflects the last applied actual.
    # Strategy: fully restore to original_amount first, then apply new actual.
    #
    # Current used_amount = original_amount +/- previous_adjustment
    # Step 1: restore back to what it was BEFORE any liquidation (i.e. original_amount deducted)
    # Step 2: apply new actual vs original difference
    #
    # We detect a previous liquidation if any confirmed ACTUAL_ITINERARY exists.
    previous_liquidated = ParticipantDocument.objects.filter(
        participant=participant,
        doc_type='ACTUAL_ITINERARY',
        is_confirmed=True,
        extracted_amount__isnull=False,
    ).exists()

    if previous_liquidated:
        # Fully reset usage back to original_amount by computing current adjustment.
        # used_amount currently = original + prev_diff (could be + or -)
        # We want used_amount = original, so:
        current_used = usage.used_amount
        target_used  = original_amount  # what it should be before new liquidation
        correction   = current_used - target_used
        if correction > 0:
            usage.restore(correction)
        elif correction < 0:
            usage.deduct(abs(correction))

    # Now apply the new actual amount vs original
    difference = actual_amount - original_amount

    if difference < 0:
        usage.restore(abs(difference))
        action        = 'returned'
        action_amount = abs(difference)
    elif difference > 0:
        usage.deduct(difference)
        action        = 'deducted'
        action_amount = difference
    else:
        action        = 'no_change'
        action_amount = Decimal('0')

    return {
        'success':         True,
        'action':          action,
        'action_amount':   action_amount,
        'original_amount': original_amount,
        'actual_amount':   actual_amount,
        'difference':      difference,
        'message': (
            f'₱{action_amount:,.2f} {action} to budget source "{source.budget_name}".'
            if action != 'no_change'
            else 'Actual matches planned amount. No budget adjustment needed.'
        ),
    }