from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Q, Count, Sum
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.cache import never_cache
from django.utils import timezone
from django.db import transaction
from decimal import Decimal
from accounts.models import User, College
from .utils import extract_budget_from_file, calculate_auto_budget
from .models import (
    TravelInitiationDocument, TravelOrder, LetterRequest,
    RegionRate, ItineraryOfTravel, PayrollParticipants, Payroll,
    OfficialTravel, FinancialDocuments, PostTravelDocuments,
    LiquidationReport, Notification,BudgetAllocation, TravelOrderComment,OfficialTravel, TravelOrder, Notification, 
    User, ItineraryOfTravel
)
# ========= For Dean =======
import json
from datetime import date, timedelta
from django.http import JsonResponse

# ==================== HELPER FUNCTIONS ====================

def get_authenticated_user(request):
    """Get the authenticated user or redirect to login"""
    if not request.session.get('user_id'):
        return None
    
    try:
        user = User.objects.get(id=request.session['user_id'])
        return user
    except User.DoesNotExist:
        request.session.flush()
        return None


def create_notification(user, notification_type, title, message, official_travel=None):
    """Create a notification for a user"""
    Notification.objects.create(
        user=user,
        notification_type=notification_type,
        title=title,
        message=message,
        official_travel=official_travel
    )


# ==================== EMPLOYEE DASHBOARD ====================

@never_cache
def employee_dashboard(request):
    """Main dashboard for employees"""
    
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to access the dashboard')
        return redirect('accounts:login')
    
    # Redirect non-employees to appropriate dashboards
    if user.role == 'ADMIN':
        return redirect('accounts:pending_approvals')
    elif user.role in ['DEPT_SEC', 'CAMPUS_SEC']:
        return redirect('travel_app:secretary_dashboard')
    elif user.role == 'DEAN':
        return redirect('travel_app:dean_dashboard')
    elif user.role == 'DIRECTOR':
        return redirect('travel_app:director_dashboard')
    elif user.role == 'PRESIDENT':
        return redirect('travel_app:president_dashboard')
    elif user.role == 'BUDGET':
        return redirect('travel_app:budget_dashboard')
    elif user.role == 'CASHIER':
        return redirect('travel_app:cashier_dashboard')
    
    # Get travels where user is a participant
    my_travels = OfficialTravel.objects.filter(
        participants_group__users=user
    ).select_related(
        'travel_order',
        'itinerary',
        'itinerary__region_rate'
    ).prefetch_related(
        'participants_group__users'
    ).order_by('-created_at')
    
    # Current/Ongoing travels
    today = timezone.now().date()
    current_travels = my_travels.filter(
        start_date__lte=today,
        end_date__gte=today,
        travel_order__status='APPROVED'
    )
    
    # Pending approval travels
    pending_travels = my_travels.filter(
        travel_order__status__in=['PENDING_DEAN', 'PENDING_DIRECTOR']
    )
    
    # Completed travels
    completed_travels = my_travels.filter(
        end_date__lt=today,
        travel_order__status='APPROVED'
    )
    
    # Recent travels (last 5)
    recent_travels = my_travels[:5]
    
    # Travels requiring post-travel documents
    travels_need_docs = []
    for travel in completed_travels:
        # Check if all required docs are submitted
        required_docs = ['COMPLETED', 'RECEIPTS', 'APPEARANCE', 'ACTUAL_ITINERARY']
        submitted_doc_types = travel.post_travel_documents.values_list('document_type', flat=True)
        
        missing_docs = [doc for doc in required_docs if doc not in submitted_doc_types]
        
        if missing_docs or not hasattr(travel, 'liquidation_report'):
            travels_need_docs.append({
                'travel': travel,
                'missing_docs': missing_docs,
                'needs_liquidation': not hasattr(travel, 'liquidation_report')
            })
    
    # Get unread notifications
    unread_notifications = Notification.objects.filter(
        user=user,
        is_read=False
    ).order_by('-created_at')[:5]
    
    # Statistics
    total_travels = my_travels.count()
    pending_count = pending_travels.count()
    completed_count = completed_travels.count()
    current_count = current_travels.count()
    
    context = {
        'title': 'Employee Dashboard - BISU Travel Management',
        'user': user,
        'current_travels': current_travels,
        'recent_travels': recent_travels,
        'pending_travels': pending_travels,
        'travels_need_docs': travels_need_docs[:5],  # Show max 5
        'unread_notifications': unread_notifications,
        'total_travels': total_travels,
        'pending_count': pending_count,
        'completed_count': completed_count,
        'current_count': current_count,
        'today': today,
    }
    
    return render(request, 'travel_app/employee/employee_dashboard.html', context)


# ==================== CREATE TRAVEL ====================

@csrf_protect
@never_cache
def create_travel(request):
    """Create a new travel request (Employee creates for themselves only) - MINIMAL VERSION"""
    
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to create a travel request')
        return redirect('accounts:login')
    
    # Only employees can use this view
    if user.role != 'EMPLOYEE':
        messages.error(request, 'Only employees can create individual travel requests here')
        return redirect('travel_app:employee_dashboard')
    
    if request.method == 'POST':
        try:
            with transaction.atomic():
                # Step 1: Upload Initiation Document
                doc_type = request.POST.get('doc_type')
                issuer = request.POST.get('issuer')
                date_issued = request.POST.get('date_issued')
                init_file = request.FILES.get('initiation_file')
                
                if not all([doc_type, issuer, date_issued, init_file]):
                    messages.error(request, 'Please fill in all initiation document fields and upload the file')
                    return redirect('travel_app:create_travel')
                
                # Create initiation document
                initiation_doc = TravelInitiationDocument.objects.create(
                    document_type=doc_type,
                    issuer=issuer,
                    date_issued=date_issued,
                    file=init_file,
                    uploaded_by=user
                )
                
                # Step 2: Basic Travel Info
                start_date = request.POST.get('start_date')
                end_date = request.POST.get('end_date')
                destination = request.POST.get('destination')
                purpose = request.POST.get('purpose')
                is_out_of_province = request.POST.get('is_out_of_province') == 'on'
                initiating_office = request.POST.get('initiating_office', user.college.name if user.college else 'N/A')
                funding_office = request.POST.get('funding_office') or initiating_office
                prepayment_option = request.POST.get('prepayment_option')
                
                # Validate required fields
                if not all([start_date, end_date, destination, purpose, prepayment_option]):
                    messages.error(request, 'Please fill in all required travel details')
                    return redirect('travel_app:create_travel')
                
                # Step 3: File Uploads
                travel_order_file = request.FILES.get('travel_order_file')
                itinerary_file = request.FILES.get('itinerary_file')
                
                if not travel_order_file:
                    messages.error(request, 'Please upload the Travel Order document')
                    return redirect('travel_app:create_travel')
                
                if not itinerary_file:
                    messages.error(request, 'Please upload the Itinerary document')
                    return redirect('travel_app:create_travel')
                
                # Step 4: Create Itinerary with HYBRID budget extraction
                try:
                    region_rate = RegionRate.objects.filter(is_active=True).first()
                    if not region_rate:
                        region_rate = RegionRate.objects.create(
                            region_code='VII',
                            region_name='Region 7',
                            meal_rate=180,
                            lodging_rate=900,
                            incidental_rate=180,
                            is_active=True
                        )
                except Exception as e:
                    messages.error(request, 'Error with region rates. Please contact administrator.')
                    return redirect('travel_app:create_travel')

                # Parse dates
                from datetime import datetime
                start = datetime.strptime(start_date, '%Y-%m-%d').date()
                end = datetime.strptime(end_date, '%Y-%m-%d').date()
                duration_days = (end - start).days + 1
                duration_nights = max(0, (end - start).days)

                # TRY HYBRID EXTRACTION FROM UPLOADED FILE
                extracted_budget = None
                extraction_method = "date-based calculation"

                if itinerary_file:
                    try:
                        extracted_budget = extract_budget_from_file(itinerary_file)
                        
                        if extracted_budget:
                            extraction_method = "file extraction"
                            messages.success(
                                request,
                                f'✅ Budget automatically extracted from file: ₱{extracted_budget:,.2f}'
                            )
                        else:
                            messages.warning(
                                request,
                                '⚠️ Could not extract budget from file. Using date-based calculation.'
                            )
                    except Exception as e:
                        messages.warning(
                            request,
                            f'⚠️ Budget extraction failed: {str(e)}. Using date-based calculation.'
                        )
                        print(f"Budget extraction error: {e}")

                # If extraction failed, calculate from dates
                if not extracted_budget:
                    extracted_budget = calculate_auto_budget(start, end, region_rate)
                    messages.info(
                        request,
                        f'📊 Calculated budget: ₱{extracted_budget:,.2f} ({duration_days} days, {duration_nights} nights)'
                    )

                # Create itinerary
                itinerary = ItineraryOfTravel.objects.create(
                    region_rate=region_rate,
                    estimated_meals_count=duration_days * 3,
                    estimated_days=duration_days,
                    estimated_nights=duration_nights,
                    estimated_transportation=0,
                    estimated_other_expenses=0,
                    file=itinerary_file
                )

                # SMART: Adjust the estimated_other_expenses to match extracted budget
                # This way the @property estimated_total will return the correct amount
                base_cost = (
                    (duration_days * 3 * region_rate.meal_rate) +
                    (duration_nights * region_rate.lodging_rate) +
                    (duration_days * region_rate.incidental_rate)
                )

                if extracted_budget > Decimal(str(base_cost)):
                    # Add the difference to "other expenses"
                    itinerary.estimated_other_expenses = int(extracted_budget - Decimal(str(base_cost)))
                    itinerary.save()
                elif extracted_budget < Decimal(str(base_cost)):
                    # Budget is less than base cost - override by reducing other components
                    # This is tricky, so we'll just set transportation to make up the difference
                    deficit = Decimal(str(base_cost)) - extracted_budget
                    itinerary.estimated_transportation = -int(deficit)  # Negative adjustment
                    itinerary.save()

                print(f"\n💾 Saved itinerary with budget: ₱{itinerary.estimated_total}")
                print(f"   Extraction method: {extraction_method}\n")
                
                # Step 5: Create Participant Group (just the employee)
                participants_group = PayrollParticipants.objects.create()
                participants_group.users.add(user)
                
                # Step 6: Create Payroll if prepayment
                payroll = None
                if prepayment_option == 'PREPAYMENT':
                    payroll = Payroll.objects.create(
                        itinerary=itinerary,
                        participants_group=participants_group
                    )
                
                # Step 7: Create Travel Order (with uploaded file)
                travel_order = TravelOrder.objects.create(
                    initiation=initiation_doc,
                    created_by=user,
                    status='PENDING_DEAN'
                )
                
                # Store the travel order file in the initiation doc for now
                # (we'll need to add a file field to TravelOrder model later)
                # For now, we'll handle it differently
                
                # Step 8: Create Letter Request if out of province
                letter_request = None
                if is_out_of_province:
                    justification = request.POST.get('justification', '')
                    letter_file = request.FILES.get('letter_file')
                    
                    if not justification:
                        messages.error(request, 'Justification is required for out-of-province travel')
                        return redirect('travel_app:create_travel')
                    
                    if not letter_file:
                        messages.error(request, 'Letter request file is required for out-of-province travel')
                        return redirect('travel_app:create_travel')
                    
                    letter_request = LetterRequest.objects.create(
                        justification=justification,
                        travel_order=travel_order,
                        file=letter_file,
                        status='PENDING'
                    )
                
                # Step 9: Create Official Travel
                official_travel = OfficialTravel.objects.create(
                    start_date=start_date,
                    end_date=end_date,
                    destination=destination,
                    is_out_of_province=is_out_of_province,
                    purpose=purpose,
                    initiating_office=initiating_office,
                    funding_office=funding_office,
                    prepayment_option=prepayment_option,
                    travel_order=travel_order,
                    itinerary=itinerary,
                    letter_request=letter_request,
                    payroll=payroll,
                    participants_group=participants_group
                )
                
                # Create notification for dean (if user has department)
                if user.college:
                    dean_users = User.objects.filter(
                        role='DEAN',
                        college=user.college
                    )
                    for dean in dean_users:
                        create_notification(
                            dean,
                            'TRAVEL_CREATED',
                            'New Travel Request',
                            f'{user.get_full_name()} submitted a travel request to {destination}',
                            official_travel
                        )
                
                # Create notification for user
                create_notification(
                    user,
                    'TRAVEL_CREATED',
                    'Travel Request Submitted',
                    f'Your travel request to {destination} has been submitted for approval',
                    official_travel
                )
                
                messages.success(
                    request,
                    f'✅ Travel request created successfully! Your travel to {destination} is pending dean approval.'
                )
                return redirect('travel_app:travel_detail', travel_id=official_travel.id)
                
        except Exception as e:
            messages.error(request, f'Error creating travel: {str(e)}')
            return redirect('travel_app:create_travel')
    
    # GET request - show form
    context = {
        'title': 'Create Travel Request - BISU Travel Management',
        'user': user,
    }
    
    return render(request, 'travel_app/employee/create_travel.html', context)

# ==================== TRAVEL HISTORY ====================

@never_cache
def travel_history(request):
    """View all travels where user is a participant"""
    
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to view travel history')
        return redirect('accounts:login')
    
    # Get all travels where user is participant
    travels = OfficialTravel.objects.filter(
        participants_group__users=user
    ).select_related(
        'travel_order',
        'itinerary',
        'itinerary__region_rate',
        'letter_request'
    ).prefetch_related(
        'participants_group__users',
        'post_travel_documents',
        'financial_documents'
    ).order_by('-created_at')
    
    # Filters
    status_filter = request.GET.get('status', '')
    year_filter = request.GET.get('year', '')
    search_query = request.GET.get('search', '')
    
    if status_filter:
        travels = travels.filter(travel_order__status=status_filter)
    
    if year_filter:
        travels = travels.filter(start_date__year=year_filter)
    
    if search_query:
        travels = travels.filter(
            Q(destination__icontains=search_query) |
            Q(purpose__icontains=search_query) |
            Q(initiating_office__icontains=search_query)
        )
    
    # Get available years
    available_years = OfficialTravel.objects.filter(
        participants_group__users=user
    ).dates('start_date', 'year', order='DESC')
    
    # Annotate with document completion status
    today = timezone.now().date()
    travels_with_status = []
    for travel in travels:
        # Check if travel is completed
        is_completed = travel.end_date and travel.end_date < today
        
        # Check document status
        required_docs = ['COMPLETED', 'RECEIPTS', 'APPEARANCE', 'ACTUAL_ITINERARY']
        submitted_docs = travel.post_travel_documents.values_list('document_type', flat=True)
        missing_docs = [doc for doc in required_docs if doc not in submitted_docs]
        
        has_liquidation = hasattr(travel, 'liquidation_report')
        
        travels_with_status.append({
            'travel': travel,
            'is_completed': is_completed,
            'missing_docs': missing_docs,
            'has_liquidation': has_liquidation,
            'needs_attention': (is_completed and (missing_docs or not has_liquidation))
        })
    
    context = {
        'title': 'Travel History - BISU Travel Management',
        'user': user,
        'travels_with_status': travels_with_status,
        'status_choices': TravelOrder.STATUS_CHOICES,
        'current_status': status_filter,
        'search_query': search_query,
        'available_years': available_years,
        'current_year': year_filter,
    }
    
    return render(request, 'travel_app/employee/travel_history.html', context)


# ==================== TRAVEL DETAIL ====================

# REPLACE the travel_detail function in travel_app/views.py with this:

@never_cache
def travel_detail(request, travel_id):
    """View detailed information about a specific travel - NOW WITH DOCUMENT HUB"""
    
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to view travel details')
        return redirect('accounts:login')
    
    # Get travel
    travel = get_object_or_404(
        OfficialTravel.objects.select_related(
            'travel_order',
            'travel_order__initiation',
            'travel_order__created_by',
            'itinerary',
            'itinerary__region_rate',
            'letter_request',
            'payroll',
            'participants_group'
        ).prefetch_related(
            'participants_group__users',
            'post_travel_documents',
            'financial_documents'
        ),
        id=travel_id
    )
    
    # Check download permission
    can_download = user_can_download_documents(user, travel)
    if not can_download:
        messages.error(request, 'You do not have permission to view this travel')
        return redirect('travel_app:employee_dashboard')
    
    # Check if travel is completed/ongoing
    today = timezone.now().date()
    is_completed = travel.end_date and travel.end_date < today
    is_ongoing = travel.start_date <= today <= travel.end_date
    is_upcoming = travel.start_date > today
    
    # Document status checks
    required_docs = {
        'COMPLETED': 'Certificate of Travel Completed',
        'RECEIPTS': 'Certificate of Not Requiring Receipts',
        'APPEARANCE': 'Certificate of Appearance',
        'ACTUAL_ITINERARY': 'Actual Itinerary of Travel'
    }
    
    submitted_doc_types = list(travel.post_travel_documents.values_list('document_type', flat=True))
    missing_docs = {k: v for k, v in required_docs.items() if k not in submitted_doc_types}
    
    has_liquidation = hasattr(travel, 'liquidation_report')
    
    # Get all documents with version history
    post_travel_docs = travel.post_travel_documents.all().order_by('document_type', '-submit_date')
    financial_docs = travel.financial_documents.all().order_by('document_type', '-upload_date')
    
    # Group financial docs by type to show versions
    financial_docs_grouped = {}
    for doc in financial_docs:
        if doc.document_type not in financial_docs_grouped:
            financial_docs_grouped[doc.document_type] = []
        financial_docs_grouped[doc.document_type].append(doc)
    
    # Check upload permissions for each document type
    can_upload_financial = user_can_upload_document(user, travel, 'DV')
    can_upload_post_travel = user_can_upload_document(user, travel, 'COMPLETED')
    can_upload_liquidation = user_can_upload_document(user, travel, 'LIQUIDATION')
    
    # Budget information
    estimated_budget = travel.itinerary.estimated_total
    if travel.payroll:
        total_budget = travel.payroll.total_estimated_expenses
    else:
        total_budget = estimated_budget
    
    context = {
        'title': f'{travel.destination} - Travel Details',
        'user': user,
        'travel': travel,
        'is_completed': is_completed,
        'is_ongoing': is_ongoing,
        'is_upcoming': is_upcoming,
        'is_participant': travel.is_participant(user),
        'can_download': can_download,
        'can_download_financial_only': can_download == 'financial_only',
        'required_docs': required_docs,
        'missing_docs': missing_docs,
        'has_liquidation': has_liquidation,
        'post_travel_docs': post_travel_docs,
        'financial_docs': financial_docs,
        'financial_docs_grouped': financial_docs_grouped,
        'estimated_budget': estimated_budget,
        'total_budget': total_budget,
        'today': today,
        # Upload permissions
        'can_upload_financial': can_upload_financial,
        'can_upload_post_travel': can_upload_post_travel,
        'can_upload_liquidation': can_upload_liquidation,
        # Document type choices
        'financial_doc_types': FinancialDocuments.DOC_TYPE_CHOICES,
        'post_travel_doc_types': PostTravelDocuments.DOC_TYPE_CHOICES,
    }
    
    return render(request, 'travel_app/shared/travel_detail.html', context)


# ==================== UPLOAD POST-TRAVEL DOCUMENTS ====================

@csrf_protect
@never_cache
def upload_post_travel_document(request, travel_id):
    """Upload post-travel documents"""
    
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to upload documents')
        return redirect('accounts:login')
    
    travel = get_object_or_404(OfficialTravel, id=travel_id)
    
    # Check permission (must be participant)
    if not travel.is_participant(user):
        messages.error(request, 'You can only upload documents for your own travels')
        return redirect('travel_app:employee_dashboard')
    
    if request.method == 'POST':
        try:
            doc_type = request.POST.get('doc_type')
            file = request.FILES.get('file')
            notes = request.POST.get('notes', '')
            
            if not all([doc_type, file]):
                messages.error(request, 'Please select document type and file')
                return redirect('travel_app:travel_detail', travel_id=travel.id)
            
            # Create document
            PostTravelDocuments.objects.create(
                document_type=doc_type,
                official_travel=travel,
                file=file,
                uploaded_by=user,
                notes=notes
            )
            
            messages.success(request, f'{dict(PostTravelDocuments.DOC_TYPE_CHOICES)[doc_type]} uploaded successfully!')
            
        except Exception as e:
            messages.error(request, f'Error uploading document: {str(e)}')
    
    return redirect('travel_app:travel_detail', travel_id=travel.id)


# ==================== SUBMIT LIQUIDATION REPORT ====================

@csrf_protect
@never_cache
def submit_liquidation(request, travel_id):
    """Submit liquidation report"""
    
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to submit liquidation')
        return redirect('accounts:login')
    
    travel = get_object_or_404(OfficialTravel, id=travel_id)
    
    # Check permission
    if not travel.is_participant(user):
        messages.error(request, 'You can only submit liquidation for your own travels')
        return redirect('travel_app:employee_dashboard')
    
    # Check if liquidation already exists
    if hasattr(travel, 'liquidation_report'):
        messages.warning(request, 'Liquidation report already submitted for this travel')
        return redirect('travel_app:travel_detail', travel_id=travel.id)
    
    if request.method == 'POST':
        try:
            total_spent = Decimal(request.POST.get('total_spent', '0'))
            file = request.FILES.get('file')
            notes = request.POST.get('notes', '')
            
            if total_spent < 0:
                messages.error(request, 'Total amount spent cannot be negative')
                return redirect('travel_app:travel_detail', travel_id=travel.id)
            
            # Create liquidation report
            liquidation = LiquidationReport.objects.create(
                official_travel=travel,
                total_amount_spent=total_spent,
                file=file,
                submitted_by=user,
                notes=notes
            )
            
            # Calculate totals
            liquidation.calculate_totals()
            
            messages.success(request, 'Liquidation report submitted successfully!')
            
            # Notify relevant parties
            if liquidation.amount_refunded > 0:
                messages.info(
                    request,
                    f'You need to refund ₱{liquidation.amount_refunded:,.2f} to the university'
                )
            elif liquidation.amount_to_be_reimbursed > 0:
                messages.info(
                    request,
                    f'You will be reimbursed ₱{liquidation.amount_to_be_reimbursed:,.2f}'
                )
            
        except Exception as e:
            messages.error(request, f'Error submitting liquidation: {str(e)}')
    
    return redirect('travel_app:travel_detail', travel_id=travel.id)


# ==================== NOTIFICATIONS ====================

@never_cache
def notifications(request):
    """View all notifications"""
    
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to view notifications')
        return redirect('accounts:login')
    
    # Get all notifications
    all_notifications = Notification.objects.filter(
        user=user
    ).select_related('official_travel').order_by('-created_at')
    
    # Mark as read if clicked
    if request.GET.get('mark_read'):
        notif_id = request.GET.get('mark_read')
        try:
            notif = Notification.objects.get(id=notif_id, user=user)
            notif.is_read = True
            notif.save()
        except:
            pass
    
    context = {
        'title': 'Notifications - BISU Travel Management',
        'user': user,
        'notifications': all_notifications,
    }
    
    return render(request, 'travel_app/employee/notifications.html', context)


@csrf_protect
def mark_notification_read(request, notif_id):
    """Mark a notification as read"""
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    
    try:
        notif = Notification.objects.get(id=notif_id, user=user)
        notif.is_read = True
        notif.save()
    except:
        pass
    
    return redirect('travel_app:notifications')

# ADD THESE VIEWS to travel_app/views.py

@csrf_protect
@never_cache
def edit_travel_budget(request, travel_id):
    """Allow user to manually correct the extracted budget"""
    
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to edit travel')
        return redirect('accounts:login')
    
    travel = get_object_or_404(OfficialTravel, id=travel_id)
    
    # Check permission (must be participant or creator)
    if not travel.is_participant(user) and travel.travel_order.created_by != user:
        messages.error(request, 'You do not have permission to edit this travel')
        return redirect('travel_app:employee_dashboard')
    
    # Can only edit if travel hasn't started yet
    today = timezone.now().date()
    if travel.start_date <= today:
        messages.error(request, 'Cannot edit budget after travel has started')
        return redirect('travel_app:travel_detail', travel_id=travel.id)
    
    # Can only edit if not yet approved
    if travel.travel_order.status not in ['PENDING_DEAN', 'PENDING_DIRECTOR']:
        messages.error(request, 'Cannot edit budget after approval')
        return redirect('travel_app:travel_detail', travel_id=travel.id)
    
    if request.method == 'POST':
        try:
            # Get new values
            new_meals = int(request.POST.get('estimated_meals', 0))
            new_days = int(request.POST.get('estimated_days', 0))
            new_nights = int(request.POST.get('estimated_nights', 0))
            new_transportation = int(request.POST.get('estimated_transportation', 0))
            new_other = int(request.POST.get('estimated_other', 0))
            
            # Update itinerary
            travel.itinerary.estimated_meals_count = new_meals
            travel.itinerary.estimated_days = new_days
            travel.itinerary.estimated_nights = new_nights
            travel.itinerary.estimated_transportation = new_transportation
            travel.itinerary.estimated_other_expenses = new_other
            travel.itinerary.save()
            
            # Update payroll if prepayment
            if travel.payroll:
                travel.payroll.save()  # This will recalculate the total
            
            messages.success(
                request,
                f'✅ Budget updated successfully! New total: ₱{travel.itinerary.estimated_total:,.2f}'
            )
            
        except Exception as e:
            messages.error(request, f'Error updating budget: {str(e)}')
    
    return redirect('travel_app:travel_detail', travel_id=travel.id)


@csrf_protect
@never_cache
def edit_travel_details(request, travel_id):
    """Allow user to edit basic travel details before approval"""
    
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to edit travel')
        return redirect('accounts:login')
    
    travel = get_object_or_404(OfficialTravel, id=travel_id)
    
    # Check permission
    if not travel.is_participant(user) and travel.travel_order.created_by != user:
        messages.error(request, 'You do not have permission to edit this travel')
        return redirect('travel_app:employee_dashboard')
    
    # Can only edit if not yet approved
    if travel.travel_order.status not in ['PENDING_DEAN', 'PENDING_DIRECTOR']:
        messages.error(request, 'Cannot edit travel after approval')
        return redirect('travel_app:travel_detail', travel_id=travel.id)
    
    if request.method == 'POST':
        try:
            # Update basic details
            travel.destination = request.POST.get('destination', travel.destination)
            travel.purpose = request.POST.get('purpose', travel.purpose)
            travel.start_date = request.POST.get('start_date', travel.start_date)
            travel.end_date = request.POST.get('end_date', travel.end_date)
            travel.save()
            
            messages.success(request, '✅ Travel details updated successfully!')
            
        except Exception as e:
            messages.error(request, f'Error updating travel: {str(e)}')
    
    return redirect('travel_app:travel_detail', travel_id=travel.id)


# ==================== PLACEHOLDER VIEWS FOR OTHER ROLES ====================
# Add these at the END of your travel_app/views.py file

@never_cache
def secretary_dashboard(request):
    """Placeholder for secretary dashboard"""
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to access the dashboard')
        return redirect('accounts:login')
    
    messages.info(request, 'Secretary dashboard is under development. Showing employee dashboard for now.')
    return redirect('travel_app:employee_dashboard')


@never_cache
def dean_dashboard(request):
    """Placeholder for dean dashboard"""
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to access the dashboard')
        return redirect('accounts:login')
    
    messages.info(request, 'Dean dashboard is under development. Showing employee dashboard for now.')
    return redirect('travel_app:employee_dashboard')


@never_cache
def director_dashboard(request):
    """Placeholder for director dashboard"""
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to access the dashboard')
        return redirect('accounts:login')
    
    messages.info(request, 'Director dashboard is under development. Showing employee dashboard for now.')
    return redirect('travel_app:employee_dashboard')


@never_cache
def president_dashboard(request):
    """Placeholder for president dashboard"""
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to access the dashboard')
        return redirect('accounts:login')
    
    messages.info(request, 'President dashboard is under development. Showing employee dashboard for now.')
    return redirect('travel_app:employee_dashboard')


@never_cache
def budget_dashboard(request):
    """Placeholder for budget officer dashboard"""
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to access the dashboard')
        return redirect('accounts:login')
    
    messages.info(request, 'Budget Officer dashboard is under development. Showing employee dashboard for now.')
    return redirect('travel_app:employee_dashboard')


@never_cache
def cashier_dashboard(request):
    """Placeholder for cashier dashboard"""
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to access the dashboard')
        return redirect('accounts:login')
    
    messages.info(request, 'Cashier dashboard is under development. Showing employee dashboard for now.')
    return redirect('travel_app:employee_dashboard')

# ==========='''This is for the travel detail document HUB'''===========
# ADD THESE HELPER FUNCTIONS TO travel_app/views.py

def user_can_download_documents(user, travel):
    """Check if user can download documents from this travel"""
    
    # Participant can download their own
    if travel.is_participant(user):
        return True
    
    # Campus Secretary can download all
    if user.role == 'CAMPUS_SEC':
        return True
    
    # Dept Secretary can download from their college
    if user.role == 'DEPT_SEC' and user.college == travel.travel_order.created_by.college:
        return True
    
    if user.role == 'DEAN' and user.college == travel.travel_order.created_by.college:
        return True
    
    # Director, President, Budget can download all
    if user.role in ['DIRECTOR', 'PRESIDENT', 'BUDGET']:
        return True
    
    # Cashier can only view financial docs (we'll handle this separately)
    if user.role == 'CASHIER':
        return 'financial_only'
    
    return False


def user_can_upload_document(user, travel, doc_type):
    """Check if user can upload specific document type"""
    
    # Financial documents - Secretary only (Dept or Campus)
    if doc_type in ['DV', 'BURS']:
        if user.role == 'CAMPUS_SEC':
            return True
        if user.role == 'DEPT_SEC' and user.college == travel.travel_order.created_by.college:
            return True
        # Budget Officer can replace if needed
        if user.role == 'BUDGET':
            return True
        return False
    
    # Post-travel documents - Participants only
    if doc_type in ['COMPLETED', 'RECEIPTS', 'APPEARANCE', 'ACTUAL_ITINERARY', 'REPORT']:
        if travel.is_participant(user):
            return True
        return False
    
    # Liquidation - Participants only, after travel ends
    if doc_type == 'LIQUIDATION':
        today = timezone.now().date()
        if travel.is_participant(user) and travel.end_date < today:
            return True
        return False
    
    return False


@csrf_protect
@never_cache
def upload_financial_document(request, travel_id):
    """Upload financial documents (DV/BURS) - Secretary/Budget only"""
    
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to upload documents')
        return redirect('accounts:login')
    
    travel = get_object_or_404(OfficialTravel, id=travel_id)
    
    if request.method == 'POST':
        doc_type = request.POST.get('doc_type')
        file = request.FILES.get('file')
        notes = request.POST.get('notes', '')
        
        # Check permission
        if not user_can_upload_document(user, travel, doc_type):
            messages.error(request, 'You do not have permission to upload this document type')
            return redirect('travel_app:travel_detail', travel_id=travel.id)
        
        if not all([doc_type, file]):
            messages.error(request, 'Please select document type and file')
            return redirect('travel_app:travel_detail', travel_id=travel.id)
        
        try:
            # Check if document already exists
            existing = FinancialDocuments.objects.filter(
                official_travel=travel,
                document_type=doc_type,
                status='ACTIVE'
            ).first()
            
            if existing:
                # Mark old as replaced
                existing.status = 'REPLACED'
                existing.save()
                
                messages.info(
                    request,
                    f'Previous {existing.get_document_type_display()} has been replaced'
                )
            
            # Create new document
            FinancialDocuments.objects.create(
                document_type=doc_type,
                official_travel=travel,
                file=file,
                uploaded_by=user,
                notes=notes,
                status='PENDING'
            )
            
            # Notify participant
            create_notification(
                travel.travel_order.created_by,
                'DOCUMENTS_UPLOADED',
                'Financial Document Uploaded',
                f'{user.get_full_name()} uploaded {dict(FinancialDocuments.DOC_TYPE_CHOICES)[doc_type]} for your travel to {travel.destination}',
                travel
            )
            
            messages.success(
                request,
                f'✅ {dict(FinancialDocuments.DOC_TYPE_CHOICES)[doc_type]} uploaded successfully!'
            )
            
        except Exception as e:
            messages.error(request, f'Error uploading document: {str(e)}')
    
    return redirect('travel_app:travel_detail', travel_id=travel.id)


def get_dean_user(request):
    """Get authenticated dean user"""
    user_id = request.session.get('user_id')
    if not user_id:
        return None
    try:
        user = User.objects.select_related('campus', 'college').get(
            id=user_id, role='DEAN'
        )
        return user
    except User.DoesNotExist:
        return None


def create_notification(user, notif_type, title, message, travel=None):
    """Helper to create notifications"""
    Notification.objects.create(
        user=user,
        notification_type=notif_type,
        title=title,
        message=message,
        official_travel=travel
    )


@never_cache
def dean_dashboard(request):
    """Main Dean Dashboard"""
    user = get_dean_user(request)
    if not user:
        messages.warning(request, 'Please login as Dean to access this page')
        return redirect('accounts:login')

    if not user.college:
        messages.error(request, 'Your account is not assigned to a college. Contact admin.')
        return redirect('accounts:profile')

    today = timezone.now().date()
    current_year = today.year

    # ── All travels in Dean's college ──
    college_travels = OfficialTravel.objects.filter(
        travel_order__created_by__college=user.college
    ).select_related(
        'travel_order',
        'travel_order__created_by',
        'travel_order__created_by__college',
        'itinerary',
        'itinerary__region_rate',
    ).prefetch_related(
        'travel_order__comments'
    )

    # ── Approval queues ──
    pending_travels  = college_travels.filter(travel_order__status='PENDING_DEAN').order_by('start_date')
    approved_travels = college_travels.filter(
        travel_order__status__in=['PENDING_DIRECTOR', 'APPROVED'],
        travel_order__approved_by_dean=user
    ).order_by('-travel_order__dean_approval_date')[:20]
    rejected_travels = college_travels.filter(
        travel_order__status='REJECTED',
        travel_order__approved_by_dean=user
    ).order_by('-travel_order__dean_approval_date')[:20]

    # ── Counts ──
    pending_count  = pending_travels.count()
    approved_count = college_travels.filter(
        travel_order__approved_by_dean=user
    ).exclude(travel_order__status='REJECTED').count()
    rejected_count = rejected_travels.count()
    total_count    = approved_count + rejected_count + pending_count

    # ── This month ──
    this_month_approved = college_travels.filter(
        travel_order__approved_by_dean=user,
        travel_order__dean_approval_date__year=today.year,
        travel_order__dean_approval_date__month=today.month
    ).count()
    this_month_rejected = college_travels.filter(
        travel_order__status='REJECTED',
        travel_order__approved_by_dean=user,
        travel_order__dean_approval_date__year=today.year,
        travel_order__dean_approval_date__month=today.month
    ).count()
    this_month_count = this_month_approved + this_month_rejected

    # ── Average approval time ──
    approved_with_dates = college_travels.filter(
        travel_order__approved_by_dean=user,
        travel_order__dean_approval_date__isnull=False
    )
    if approved_with_dates.exists():
        total_days = sum([
            (t.travel_order.dean_approval_date.date() - t.travel_order.date_issued).days
            for t in approved_with_dates
        ])
        avg_approval_time = round(total_days / approved_with_dates.count(), 1)
    else:
        avg_approval_time = 0

    # ── Monthly trend (last 6 months) ──
    monthly_labels = []
    monthly_data   = []
    for i in range(5, -1, -1):
        month_date = today.replace(day=1) - timedelta(days=i * 30)
        label = month_date.strftime('%b')
        count = college_travels.filter(
            travel_order__approved_by_dean=user,
            travel_order__dean_approval_date__year=month_date.year,
            travel_order__dean_approval_date__month=month_date.month
        ).count()
        monthly_labels.append(label)
        monthly_data.append(count)

    # ── Top travelers ──
    from django.db.models import Count as DCount
    top_travelers_qs = User.objects.filter(
        college=user.college
    ).annotate(
        travel_count=DCount(
            'participant_groups__official_travels',
            filter=Q(participant_groups__official_travels__start_date__year=current_year)
        )
    ).filter(travel_count__gt=0).order_by('-travel_count')[:5]

    max_travels = top_travelers_qs.first().travel_count if top_travelers_qs.exists() else 1
    top_travelers = []
    for emp in top_travelers_qs:
        emp.travel_pct = round((emp.travel_count / max_travels) * 100)
        top_travelers.append(emp)

    # ── College budget ──
    college_budget = BudgetAllocation.objects.filter(
        college=user.college,
        fiscal_year=current_year
    ).first()

    # ── Calendar travel dates ──
    travel_dates = {}
    for travel in college_travels.filter(
        start_date__year=today.year,
        start_date__month__in=[today.month - 1, today.month, today.month + 1]
    ):
        current = travel.start_date
        end     = travel.end_date or travel.start_date
        status  = travel.travel_order.status

        while current <= end:
            date_str = current.strftime('%Y-%m-%d')
            travel_dates[date_str] = {
                'type':  'pending' if status == 'PENDING_DEAN' else 'approved',
                'label': f"{travel.destination} - {travel.travel_order.created_by.get_full_name()}"
            }
            current += timedelta(days=1)

    # ── College employees ──
    college_employees = User.objects.filter(
        college=user.college,
        is_approved=True,
        is_active=True
    ).exclude(role__in=['DEAN', 'ADMIN']).annotate(
        travel_count=DCount('participant_groups__official_travels')
    )

    # Flag employees with pending liquidation
    for emp in college_employees:
        emp.has_pending_liquidation = OfficialTravel.objects.filter(
            participants_group__users=emp,
            end_date__lt=today
        ).exclude(liquidation_report__isnull=False).exists()

    # ── Urgency dates ──
    today_plus_3 = today + timedelta(days=3)
    today_plus_7 = today + timedelta(days=7)

    # ── Notifications ──
    unread_notifications = Notification.objects.filter(
        user=user,
        is_read=False
    ).count()

    # ── Available years for stats dropdown ──
    available_years = list(range(current_year - 2, current_year + 1))

    context = {
        'title':               'Dean Dashboard',
        'user':                user,
        'today':               today,
        'today_plus_3':        today_plus_3,
        'today_plus_7':        today_plus_7,
        'current_year':        current_year,
        'available_years':     available_years,

        # Approval queues
        'pending_travels':     pending_travels,
        'approved_travels':    approved_travels,
        'rejected_travels':    rejected_travels,

        # Counts
        'pending_count':       pending_count,
        'approved_count':      approved_count,
        'rejected_count':      rejected_count,
        'total_count':         total_count,
        'this_month_count':    this_month_count,
        'this_month_approved': this_month_approved,
        'this_month_rejected': this_month_rejected,
        'avg_approval_time':   avg_approval_time,

        # Charts
        'monthly_labels':      json.dumps(monthly_labels),
        'monthly_data':        json.dumps(monthly_data),
        'top_travelers':       top_travelers,

        # Budget
        'college_budget':      college_budget,

        # Calendar
        'travel_dates_json':   json.dumps(travel_dates),

        # Employees
        'college_employees':   college_employees,

        # Notifications
        'unread_notifications': unread_notifications,
    }

    return render(request, 'travel_app/dean/dashboard.html', context)


@csrf_protect
@never_cache
def dean_approve(request):
    """Handle single travel approval or rejection"""
    user = get_dean_user(request)
    if not user:
        return redirect('accounts:login')

    if request.method != 'POST':
        return redirect('travel_app:dean_dashboard')

    travel_id = request.POST.get('travel_id')
    action    = request.POST.get('action')
    remarks   = request.POST.get('remarks', '').strip()
    rejection_reason = request.POST.get('rejection_reason', '').strip()

    travel = get_object_or_404(
        OfficialTravel,
        id=travel_id,
        travel_order__created_by__college=user.college
    )

    if not travel.travel_order.can_dean_approve():
        messages.error(request, 'This travel order is no longer pending dean approval.')
        return redirect('travel_app:dean_dashboard')

    now = timezone.now()

    if action == 'approve':
        travel.travel_order.status              = 'PENDING_DIRECTOR'
        travel.travel_order.approved_by_dean    = user
        travel.travel_order.dean_approval_date  = now
        travel.travel_order.dean_remarks        = remarks
        travel.travel_order.save()

        # Notify the employee
        create_notification(
            user=travel.travel_order.created_by,
            notif_type='TRAVEL_APPROVED',
            title='Travel Approved by Dean',
            message=f'Your travel to {travel.destination} has been approved by Dean {user.get_full_name()}. Forwarded to Director for final approval.',
            travel=travel
        )

        messages.success(
            request,
            f'✅ Travel to {travel.destination} approved! Forwarded to Director.'
        )

    elif action == 'reject':
        travel.travel_order.status           = 'REJECTED'
        travel.travel_order.approved_by_dean = user
        travel.travel_order.dean_approval_date = now
        travel.travel_order.rejection_reason = rejection_reason
        travel.travel_order.save()

        # Notify the employee
        create_notification(
            user=travel.travel_order.created_by,
            notif_type='TRAVEL_REJECTED',
            title='Travel Rejected by Dean',
            message=f'Your travel to {travel.destination} has been rejected by Dean {user.get_full_name()}. Reason: {rejection_reason or "No reason provided"}',
            travel=travel
        )

        messages.warning(
            request,
            f'❌ Travel to {travel.destination} rejected.'
        )

    return redirect('travel_app:dean_dashboard')


@csrf_protect
@never_cache
def dean_batch_approve(request):
    """Handle batch approval or rejection"""
    user = get_dean_user(request)
    if not user:
        return redirect('accounts:login')

    if request.method != 'POST':
        return redirect('travel_app:dean_dashboard')

    travel_ids    = request.POST.get('travel_ids', '').split(',')
    action        = request.POST.get('action')
    batch_remarks = request.POST.get('batch_remarks', '').strip()

    if not travel_ids or not action:
        messages.error(request, 'Invalid batch action.')
        return redirect('travel_app:dean_dashboard')

    travels = OfficialTravel.objects.filter(
        id__in=travel_ids,
        travel_order__created_by__college=user.college,
        travel_order__status='PENDING_DEAN'
    )

    now           = timezone.now()
    success_count = 0

    for travel in travels:
        if action == 'approve':
            travel.travel_order.status             = 'PENDING_DIRECTOR'
            travel.travel_order.approved_by_dean   = user
            travel.travel_order.dean_approval_date = now
            travel.travel_order.dean_remarks       = batch_remarks
            travel.travel_order.save()

            create_notification(
                user=travel.travel_order.created_by,
                notif_type='TRAVEL_APPROVED',
                title='Travel Approved by Dean',
                message=f'Your travel to {travel.destination} has been approved by Dean {user.get_full_name()}.',
                travel=travel
            )

        elif action == 'reject':
            travel.travel_order.status             = 'REJECTED'
            travel.travel_order.approved_by_dean   = user
            travel.travel_order.dean_approval_date = now
            travel.travel_order.rejection_reason   = batch_remarks
            travel.travel_order.save()

            create_notification(
                user=travel.travel_order.created_by,
                notif_type='TRAVEL_REJECTED',
                title='Travel Rejected by Dean',
                message=f'Your travel to {travel.destination} has been rejected by Dean {user.get_full_name()}.',
                travel=travel
            )

        success_count += 1

    verb = 'approved' if action == 'approve' else 'rejected'
    messages.success(request, f'✅ {success_count} travel(s) {verb} successfully!')
    return redirect('travel_app:dean_dashboard')


@never_cache
def dean_employee_history(request, employee_id):
    """AJAX endpoint - employee travel history"""
    user = get_dean_user(request)
    if not user:
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    employee = get_object_or_404(
        User,
        id=employee_id,
        college=user.college
    )

    travels = OfficialTravel.objects.filter(
        participants_group__users=employee
    ).select_related('travel_order', 'itinerary').order_by('-start_date')

    today = timezone.now().date()

    travel_data = []
    for t in travels:
        has_pending_liq = (
            t.end_date and t.end_date < today and
            not hasattr(t, 'liquidation_report')
        )
        travel_data.append({
            'id':           t.id,
            'destination':  t.destination,
            'dates':        f"{t.start_date.strftime('%b %d')} – {t.end_date.strftime('%b %d, %Y') if t.end_date else 'TBD'}",
            'budget':       f"{t.itinerary.estimated_total:,.0f}",
            'status':       t.travel_order.status,
            'status_display': t.travel_order.get_status_display(),
            'has_pending_liquidation': has_pending_liq,
        })

    total    = len(travel_data)
    approved = sum(1 for t in travel_data if t['status'] in ['PENDING_DIRECTOR', 'APPROVED'])
    pending  = sum(1 for t in travel_data if t['status'] == 'PENDING_DEAN')

    return JsonResponse({
        'travels':  travel_data,
        'total':    total,
        'approved': approved,
        'pending':  pending,
    })


@csrf_protect
@never_cache
def add_comment(request, travel_order_id):
    """Add comment to travel order"""
    user_id = request.session.get('user_id')
    if not user_id:
        return redirect('accounts:login')

    if request.method != 'POST':
        return redirect('travel_app:dean_dashboard')

    user  = get_object_or_404(User, id=user_id)
    order = get_object_or_404(TravelOrder, id=travel_order_id)
    msg   = request.POST.get('message', '').strip()

    if not msg:
        messages.error(request, 'Comment cannot be empty.')
    else:
        TravelOrderComment.objects.create(
            travel_order=order,
            author=user,
            message=msg
        )

        # Notify the other party
        travel = order.official_travels.first()

        if user.role == 'DEAN':
            # Notify the employee
            create_notification(
                user=order.created_by,
                notif_type='COMMENT_ADDED',
                title='Dean added a comment',
                message=f'Dean {user.get_full_name()} commented on your travel to {travel.destination if travel else ""}.',
                travel=travel
            )
        else:
            # Notify the dean
            dean = User.objects.filter(
                role='DEAN',
                college=user.college
            ).first()
            if dean and travel:
                create_notification(
                    user=dean,
                    notif_type='COMMENT_ADDED',
                    title='Employee replied to comment',
                    message=f'{user.get_full_name()} replied on travel to {travel.destination}.',
                    travel=travel
                )

        messages.success(request, 'Comment added.')

    # Redirect back to where the user came from
    next_url = request.POST.get('next') or request.META.get('HTTP_REFERER', '/')
    return redirect(next_url)


@never_cache
def approval_history(request):
    """Full approval history page for Dean"""
    user = get_dean_user(request)
    if not user:
        return redirect('accounts:login')

    travels = OfficialTravel.objects.filter(
        travel_order__created_by__college=user.college,
        travel_order__approved_by_dean=user
    ).select_related(
        'travel_order',
        'travel_order__created_by',
        'itinerary'
    ).order_by('-travel_order__dean_approval_date')

    context = {
        'title':   'Approval History',
        'user':    user,
        'travels': travels,
    }
    return render(request, 'travel_app/dean/approval_history.html', context)


@never_cache
def dean_notifications(request):
    """Dean notifications page"""
    user = get_dean_user(request)
    if not user:
        return redirect('accounts:login')

    notifications = Notification.objects.filter(user=user).order_by('-created_at')

    # Mark all as read
    notifications.filter(is_read=False).update(is_read=True)

    context = {
        'title':         'Notifications',
        'user':          user,
        'notifications': notifications,
    }
    return render(request, 'travel_app/dean/notifications.html', context)

# ==================== UPDATED travel_detail VIEW ====================
# REPLACE the existing travel_detail function with this version

@never_cache
def travel_detail(request, travel_id):
    """
    Role-universal travel detail view.
    Access rules:
      - EMPLOYEE       → own travels only (participant)
      - DEPT_SEC       → same college travels
      - DEAN           → same college travels
      - CAMPUS_SEC     → all travels on campus
      - DIRECTOR       → all travels
      - PRESIDENT      → all travels
      - BUDGET         → all travels
      - CASHIER        → all travels (financial docs only)
    """
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to view travel details')
        return redirect('accounts:login')

    travel = get_object_or_404(
        OfficialTravel.objects.select_related(
            'travel_order',
            'travel_order__initiation',
            'travel_order__created_by',
            'travel_order__approved_by_dean',
            'travel_order__approved_by_director',
            'itinerary',
            'itinerary__region_rate',
            'letter_request',
            'payroll',
            'participants_group'
        ).prefetch_related(
            'participants_group__users',
            'post_travel_documents',
            'financial_documents'
        ),
        id=travel_id
    )

    # ── PERMISSION CHECK ──
    can_download = user_can_download_documents(user, travel)
    if not can_download:
        messages.error(request, 'You do not have permission to view this travel')
        # Redirect to the user's own dashboard
        dashboard_map = {
            'DEAN': 'travel_app:dean_dashboard',
            'DIRECTOR': 'travel_app:director_dashboard',
            'PRESIDENT': 'travel_app:president_dashboard',
            'DEPT_SEC': 'travel_app:secretary_dashboard',
            'CAMPUS_SEC': 'travel_app:secretary_dashboard',
            'BUDGET': 'travel_app:budget_dashboard',
            'CASHIER': 'travel_app:cashier_dashboard',
        }
        return redirect(dashboard_map.get(user.role, 'travel_app:employee_dashboard'))

    today = timezone.now().date()
    is_completed = travel.end_date and travel.end_date < today
    is_ongoing   = travel.start_date <= today <= travel.end_date if travel.end_date else False
    is_upcoming  = travel.start_date > today

    # ── DOCUMENT STATUS ──
    required_docs = {
        'COMPLETED':       'Certificate of Travel Completed',
        'RECEIPTS':        'Certificate of Not Requiring Receipts',
        'APPEARANCE':      'Certificate of Appearance',
        'ACTUAL_ITINERARY':'Actual Itinerary of Travel',
    }
    submitted_doc_types = list(travel.post_travel_documents.values_list('document_type', flat=True))
    missing_docs        = {k: v for k, v in required_docs.items() if k not in submitted_doc_types}
    has_liquidation     = hasattr(travel, 'liquidation_report')

    post_travel_docs = travel.post_travel_documents.all().order_by('document_type', '-submit_date')
    financial_docs   = travel.financial_documents.all().order_by('document_type', '-upload_date')

    financial_docs_grouped = {}
    for doc in financial_docs:
        financial_docs_grouped.setdefault(doc.document_type, []).append(doc)

    # ── UPLOAD PERMISSIONS ──
    can_upload_financial    = user_can_upload_document(user, travel, 'DV')
    can_upload_post_travel  = user_can_upload_document(user, travel, 'COMPLETED')
    can_upload_liquidation  = user_can_upload_document(user, travel, 'LIQUIDATION')

    estimated_budget = travel.itinerary.estimated_total
    total_budget     = (
        travel.payroll.total_estimated_expenses
        if travel.payroll else estimated_budget
    )

    context = {
        'title': f'{travel.destination} - Travel Details',
        'user': user,
        'travel': travel,
        'is_completed': is_completed,
        'is_ongoing': is_ongoing,
        'is_upcoming': is_upcoming,
        'is_participant': travel.is_participant(user),
        'can_download': can_download,
        'can_download_financial_only': can_download == 'financial_only',
        'required_docs': required_docs,
        'missing_docs': missing_docs,
        'has_liquidation': has_liquidation,
        'post_travel_docs': post_travel_docs,
        'financial_docs': financial_docs,
        'financial_docs_grouped': financial_docs_grouped,
        'estimated_budget': estimated_budget,
        'total_budget': total_budget,
        'today': today,
        'can_upload_financial': can_upload_financial,
        'can_upload_post_travel': can_upload_post_travel,
        'can_upload_liquidation': can_upload_liquidation,
        'financial_doc_types': FinancialDocuments.DOC_TYPE_CHOICES,
        'post_travel_doc_types': PostTravelDocuments.DOC_TYPE_CHOICES,
    }

    return render(request, 'travel_app/shared/travel_detail.html', context)


# ==================== UPDATED user_can_download_documents HELPER ====================
# REPLACE the existing helper with this version

def user_can_download_documents(user, travel):
    """
    Returns True if the user can see all documents,
    'financial_only' for cashier,
    or False if no access.
    """
    # Participant always sees their own
    if travel.is_participant(user):
        return True

    # Campus Secretary sees all on campus
    if user.role == 'CAMPUS_SEC':
        return True

    # Dept Secretary sees their college
    if user.role == 'DEPT_SEC':
        if user.college and user.college == travel.travel_order.created_by.college:
            return True
        return False

    # Dean sees their college
    if user.role == 'DEAN':
        if user.college and user.college == travel.travel_order.created_by.college:
            return True
        return False

    # Director, President, Budget see all
    if user.role in ['DIRECTOR', 'PRESIDENT', 'BUDGET']:
        return True

    # Cashier sees only financial docs
    if user.role == 'CASHIER':
        return 'financial_only'

    return False


# ==================== NEW: DEAN CREATE TRAVEL VIEW ====================
# ADD this new view to travel_app/views.py

@csrf_protect
@never_cache
def dean_create_travel(request):
    """
    Allows a Dean to create a travel request for themselves.
    The Dean is automatically added as the sole participant.
    The travel order is submitted as PENDING_DEAN so the Dean
    can self-approve it from their dashboard (same as any pending travel).
    """
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to create a travel request')
        return redirect('accounts:login')

    if user.role != 'DEAN':
        messages.error(request, 'Only Deans can access this page')
        return redirect('travel_app:employee_dashboard')

    if request.method == 'POST':
        try:
            with transaction.atomic():
                # ── Step 1: Initiation Document ──
                doc_type   = request.POST.get('doc_type')
                issuer     = request.POST.get('issuer')
                date_issued = request.POST.get('date_issued')
                init_file  = request.FILES.get('initiation_file')

                if not all([doc_type, issuer, date_issued, init_file]):
                    messages.error(request, 'Please fill in all initiation document fields and upload the file')
                    return redirect('travel_app:dean_create_travel')

                initiation_doc = TravelInitiationDocument.objects.create(
                    document_type=doc_type,
                    issuer=issuer,
                    date_issued=date_issued,
                    file=init_file,
                    uploaded_by=user
                )

                # ── Step 2: Travel Details ──
                start_date        = request.POST.get('start_date')
                end_date          = request.POST.get('end_date')
                destination       = request.POST.get('destination')
                purpose           = request.POST.get('purpose')
                is_out_of_province = request.POST.get('is_out_of_province') == 'on'
                initiating_office = request.POST.get('initiating_office', user.college.name if user.college else 'N/A')
                funding_office    = request.POST.get('funding_office') or initiating_office
                prepayment_option = request.POST.get('prepayment_option')

                if not all([start_date, end_date, destination, purpose, prepayment_option]):
                    messages.error(request, 'Please fill in all required travel details')
                    return redirect('travel_app:dean_create_travel')

                # ── Step 3: Files ──
                travel_order_file = request.FILES.get('travel_order_file')
                itinerary_file    = request.FILES.get('itinerary_file')

                if not travel_order_file:
                    messages.error(request, 'Please upload the Travel Order document')
                    return redirect('travel_app:dean_create_travel')

                if not itinerary_file:
                    messages.error(request, 'Please upload the Itinerary document')
                    return redirect('travel_app:dean_create_travel')

                # ── Step 4: Region Rate & Budget ──
                from datetime import datetime as dt
                start = dt.strptime(start_date, '%Y-%m-%d').date()
                end   = dt.strptime(end_date, '%Y-%m-%d').date()

                if end < start:
                    messages.error(request, 'End date must be on or after the start date')
                    return redirect('travel_app:dean_create_travel')

                duration_days   = (end - start).days + 1
                duration_nights = max(0, (end - start).days)

                region_rate = RegionRate.objects.filter(is_active=True).first()
                if not region_rate:
                    region_rate = RegionRate.objects.create(
                        region_code='VII',
                        region_name='Region 7',
                        meal_rate=180,
                        lodging_rate=900,
                        incidental_rate=180,
                        is_active=True
                    )

                # Try budget extraction
                extracted_budget = None
                try:
                    extracted_budget = extract_budget_from_file(itinerary_file)
                    if extracted_budget:
                        messages.success(request, f'✅ Budget extracted from file: ₱{extracted_budget:,.2f}')
                    else:
                        messages.warning(request, '⚠️ Could not extract budget. Using date-based calculation.')
                except Exception as e:
                    messages.warning(request, f'⚠️ Budget extraction failed. Using date-based calculation.')

                if not extracted_budget:
                    extracted_budget = calculate_auto_budget(start, end, region_rate)
                    messages.info(request, f'📊 Calculated budget: ₱{extracted_budget:,.2f}')

                itinerary = ItineraryOfTravel.objects.create(
                    region_rate=region_rate,
                    estimated_meals_count=duration_days * 3,
                    estimated_days=duration_days,
                    estimated_nights=duration_nights,
                    estimated_transportation=0,
                    estimated_other_expenses=0,
                    file=itinerary_file
                )

                # Adjust other_expenses to match extracted budget
                base_cost = (
                    (duration_days * 3 * region_rate.meal_rate) +
                    (duration_nights * region_rate.lodging_rate) +
                    (duration_days * region_rate.incidental_rate)
                )
                if extracted_budget > Decimal(str(base_cost)):
                    itinerary.estimated_other_expenses = int(extracted_budget - Decimal(str(base_cost)))
                    itinerary.save()
                elif extracted_budget < Decimal(str(base_cost)):
                    deficit = Decimal(str(base_cost)) - extracted_budget
                    itinerary.estimated_transportation = -int(deficit)
                    itinerary.save()

                # ── Step 5: Participant Group (Dean only) ──
                participants_group = PayrollParticipants.objects.create()
                participants_group.users.add(user)

                # ── Step 6: Payroll if prepayment ──
                payroll = None
                if prepayment_option == 'PREPAYMENT':
                    payroll = Payroll.objects.create(
                        itinerary=itinerary,
                        participants_group=participants_group
                    )

                # ── Step 7: Travel Order → PENDING_DEAN ──
                travel_order = TravelOrder.objects.create(
                    initiation=initiation_doc,
                    created_by=user,
                    status='PENDING_DEAN'
                )

                # ── Step 8: Letter Request if out-of-province ──
                letter_request = None
                if is_out_of_province:
                    justification = request.POST.get('justification', '')
                    letter_file   = request.FILES.get('letter_file')

                    if not justification:
                        messages.error(request, 'Justification is required for out-of-province travel')
                        return redirect('travel_app:dean_create_travel')

                    if not letter_file:
                        messages.error(request, 'Letter request file is required for out-of-province travel')
                        return redirect('travel_app:dean_create_travel')

                    letter_request = LetterRequest.objects.create(
                        justification=justification,
                        travel_order=travel_order,
                        file=letter_file,
                        status='PENDING'
                    )

                # ── Step 9: Official Travel ──
                official_travel = OfficialTravel.objects.create(
                    start_date=start_date,
                    end_date=end_date,
                    destination=destination,
                    is_out_of_province=is_out_of_province,
                    purpose=purpose,
                    initiating_office=initiating_office,
                    funding_office=funding_office,
                    prepayment_option=prepayment_option,
                    travel_order=travel_order,
                    itinerary=itinerary,
                    letter_request=letter_request,
                    payroll=payroll,
                    participants_group=participants_group
                )

                # Notify the dean themselves (confirmation)
                create_notification(
                    user,
                    'TRAVEL_CREATED',
                    'Travel Request Submitted',
                    f'Your travel request to {destination} has been submitted. '
                    f'Go to your Pending Approvals to approve it and forward to the Director.',
                    official_travel
                )

                messages.success(
                    request,
                    f'✅ Travel request to {destination} submitted! '
                    f'You can now approve it from your Pending Approvals queue.'
                )
                return redirect('travel_app:travel_detail', travel_id=official_travel.id)

        except Exception as e:
            messages.error(request, f'Error creating travel: {str(e)}')
            return redirect('travel_app:dean_create_travel')

    context = {
        'title': 'Create Travel Request - Dean',
        'user': user,
    }
    return render(request, 'travel_app/dean/dean_create_travel.html', context)


# ==================== ADD THESE VIEWS TO travel_app/views.py ====================

# ── DEAN TRAVEL HISTORY ──
@never_cache
def dean_travel_history(request):
    """Show all travels where the Dean is a participant (not just approved)"""
    user = get_dean_user(request)
    if not user:
        messages.warning(request, 'Please login as Dean')
        return redirect('accounts:login')

    # Get all travels where Dean is participant
    travels = OfficialTravel.objects.filter(
        participants_group__users=user
    ).select_related(
        'travel_order',
        'itinerary',
        'itinerary__region_rate',
        'letter_request'
    ).prefetch_related(
        'participants_group__users',
        'post_travel_documents',
        'financial_documents'
    ).order_by('-created_at')

    # Filters
    status_filter = request.GET.get('status', '')
    year_filter   = request.GET.get('year', '')
    search_query  = request.GET.get('search', '')

    if status_filter:
        travels = travels.filter(travel_order__status=status_filter)
    if year_filter:
        travels = travels.filter(start_date__year=year_filter)
    if search_query:
        travels = travels.filter(
            Q(destination__icontains=search_query) |
            Q(purpose__icontains=search_query) |
            Q(initiating_office__icontains=search_query)
        )

    # Available years
    available_years = OfficialTravel.objects.filter(
        participants_group__users=user
    ).dates('start_date', 'year', order='DESC')

    # Annotate with status
    today = timezone.now().date()
    travels_with_status = []
    for travel in travels:
        is_completed = travel.end_date and travel.end_date < today
        is_ongoing   = travel.start_date <= today <= travel.end_date if travel.end_date else False

        required_docs = ['COMPLETED', 'RECEIPTS', 'APPEARANCE', 'ACTUAL_ITINERARY']
        submitted_docs = travel.post_travel_documents.values_list('document_type', flat=True)
        missing_docs = [doc for doc in required_docs if doc not in submitted_docs]

        has_liquidation = hasattr(travel, 'liquidation_report')

        travels_with_status.append({
            'travel': travel,
            'is_completed': is_completed,
            'is_ongoing': is_ongoing,
            'missing_docs': missing_docs,
            'has_liquidation': has_liquidation,
            'needs_attention': (is_completed and (missing_docs or not has_liquidation))
        })

    context = {
        'title': 'My Travel History',
        'user': user,
        'travels_with_status': travels_with_status,
        'status_choices': TravelOrder.STATUS_CHOICES,
        'current_status': status_filter,
        'search_query': search_query,
        'available_years': available_years,
        'current_year': year_filter,
        'total_count': travels.count(),
        'completed_count': len([t for t in travels_with_status if t['is_completed']]),
    }

    return render(request, 'travel_app/dean/dean_travel_history.html', context)


# ── DEAN NOTIFICATIONS (improved) ──
@never_cache
def dean_notifications(request):
    """Dean notifications page - improved version"""
    user = get_dean_user(request)
    if not user:
        return redirect('accounts:login')

    notifications = Notification.objects.filter(user=user).order_by('-created_at')

    # Count by category
    unread_count   = notifications.filter(is_read=False).count()
    approval_count = notifications.filter(notification_type__icontains='APPROVAL').count()
    travel_count   = notifications.filter(notification_type__icontains='TRAVEL').count()

    # Mark all as read (auto-mark when viewing)
    notifications.filter(is_read=False).update(is_read=True)

    context = {
        'title': 'Notifications',
        'user': user,
        'notifications': notifications,
        'unread_count': unread_count,
        'approval_count': approval_count,
        'travel_count': travel_count,
    }
    return render(request, 'travel_app/dean/dean_notifications.html', context)


# ── DEAN MARK ALL READ ──
@csrf_protect
@never_cache
def dean_mark_all_read(request):
    """Mark all notifications as read"""
    user = get_dean_user(request)
    if not user:
        return redirect('accounts:login')

    if request.method == 'POST':
        Notification.objects.filter(user=user, is_read=False).update(is_read=True)
        messages.success(request, 'All notifications marked as read')

    return redirect('travel_app:dean_notifications')


# ── DEAN MARK SINGLE NOTIF READ ──
@csrf_protect
@never_cache
def dean_mark_notif_read(request, notif_id):
    """Mark a single notification as read"""
    user = get_dean_user(request)
    if not user:
        return redirect('accounts:login')

    try:
        notif = Notification.objects.get(id=notif_id, user=user)
        notif.is_read = True
        notif.save()
    except:
        pass

    return redirect('travel_app:dean_notifications')


# ── DEAN REPORTS & ANALYTICS ──
@never_cache
def dean_reports(request):
    """Analytics and reports dashboard for Dean"""
    user = get_dean_user(request)
    if not user:
        messages.warning(request, 'Please login as Dean')
        return redirect('accounts:login')

    if not user.college:
        messages.error(request, 'Your account is not assigned to a college.')
        return redirect('accounts:profile')

    today = timezone.now().date()
    current_year = int(request.GET.get('year', today.year))
    available_years = list(range(current_year - 2, current_year + 1))

    # All college travels for the selected year
    college_travels = OfficialTravel.objects.filter(
        travel_order__created_by__college=user.college,
        start_date__year=current_year
    ).select_related('travel_order', 'itinerary')

    total_travels = college_travels.count()
    approved_count = college_travels.filter(travel_order__status='APPROVED').count()
    approval_rate = round((approved_count / total_travels * 100) if total_travels > 0 else 0, 1)

    # Year-over-year comparison
    last_year_count = OfficialTravel.objects.filter(
        travel_order__created_by__college=user.college,
        start_date__year=current_year - 1
    ).count()
    this_year_increase = round(((total_travels - last_year_count) / last_year_count * 100) if last_year_count > 0 else 0, 1)

    # Budget calculations
    total_budget = sum([t.itinerary.estimated_total for t in college_travels])
    college_budget = BudgetAllocation.objects.filter(
        college=user.college,
        fiscal_year=current_year
    ).first()
    budget_utilization = college_budget.utilization_percentage if college_budget else 0

    # Approval time
    approved_travels = college_travels.filter(
        travel_order__approved_by_dean__isnull=False,
        travel_order__dean_approval_date__isnull=False
    )
    if approved_travels.exists():
        total_days = sum([
            (t.travel_order.dean_approval_date.date() - t.travel_order.date_issued).days
            for t in approved_travels
        ])
        avg_approval_days = round(total_days / approved_travels.count(), 1)
    else:
        avg_approval_days = 0
    approval_improvement = 15  # placeholder

    # Monthly trend
    from django.db.models.functions import TruncMonth
    monthly_data_qs = college_travels.annotate(
        month=TruncMonth('start_date')
    ).values('month').annotate(count=Count('id')).order_by('month')

    monthly_labels = [item['month'].strftime('%b') for item in monthly_data_qs]
    monthly_data = [item['count'] for item in monthly_data_qs]

    # Travel type breakdown
    in_province_count = college_travels.filter(is_out_of_province=False).count()
    out_of_province_count = college_travels.filter(is_out_of_province=True).count()
    prepayment_count = college_travels.filter(prepayment_option='PREPAYMENT').count()
    no_prepayment_count = college_travels.filter(prepayment_option='NOT').count()

    # Top destinations
    destinations_qs = college_travels.values('destination').annotate(
        count=Count('id')
    ).order_by('-count')[:10]
    max_dest = destinations_qs.first()['count'] if destinations_qs.exists() else 1
    top_destinations = []
    for dest in destinations_qs:
        dest['percentage'] = round((dest['count'] / max_dest) * 100)
        top_destinations.append(dest)

    # Performance metrics
    active_travelers = User.objects.filter(
        college=user.college,
        participant_groups__official_travels__start_date__year=current_year
    ).distinct().count()

    total_participants = sum([t.participants_group.users.count() for t in college_travels])

    durations = [t.get_duration_days() for t in college_travels]
    avg_duration = round(sum(durations) / len(durations)) if durations else 0

    avg_budget = round(total_budget / total_travels) if total_travels > 0 else 0

    # Compliance
    completed_travels = college_travels.filter(end_date__lt=today, travel_order__status='APPROVED')
    if completed_travels.exists():
        with_post_docs = completed_travels.filter(post_travel_documents__isnull=False).distinct().count()
        post_travel_compliance = round((with_post_docs / completed_travels.count()) * 100)

        with_liq = completed_travels.filter(liquidation_report__isnull=False).count()
        liquidation_compliance = round((with_liq / completed_travels.count()) * 100)
    else:
        post_travel_compliance = 0
        liquidation_compliance = 0

    approved_travels_count = college_travels.filter(travel_order__status='APPROVED').count()
    if approved_travels_count > 0:
        with_financial = college_travels.filter(
            travel_order__status='APPROVED',
            financial_documents__isnull=False
        ).distinct().count()
        financial_compliance = round((with_financial / approved_travels_count) * 100)
    else:
        financial_compliance = 0

    context = {
        'title': 'Reports & Analytics',
        'user': user,
        'current_year': current_year,
        'available_years': available_years,
        'total_travels': total_travels,
        'this_year_increase': this_year_increase,
        'approval_rate': approval_rate,
        'approved_count': approved_count,
        'total_budget': total_budget / 1000,  # in thousands
        'budget_utilization': budget_utilization,
        'avg_approval_days': avg_approval_days,
        'approval_improvement': approval_improvement,
        'monthly_labels': json.dumps(monthly_labels),
        'monthly_data': json.dumps(monthly_data),
        'in_province_count': in_province_count,
        'out_of_province_count': out_of_province_count,
        'prepayment_count': prepayment_count,
        'no_prepayment_count': no_prepayment_count,
        'top_destinations': top_destinations,
        'active_travelers': active_travelers,
        'total_participants': total_participants,
        'avg_duration': avg_duration,
        'avg_budget': avg_budget,
        'post_travel_compliance': post_travel_compliance,
        'liquidation_compliance': liquidation_compliance,
        'financial_compliance': financial_compliance,
    }

    return render(request, 'travel_app/dean/dean_reports.html', context)


# ── DEAN EXPORT REPORT (placeholder) ──
@never_cache
def export_dean_report(request):
    """Export Dean report as PDF or Excel - placeholder"""
    messages.info(request, 'Export feature coming soon!')
    return redirect('travel_app:dean_reports')


# Helper function to get director user
def get_director_user(request):
    """Get director user or redirect"""
    if not request.user.is_authenticated:
        return None
    if request.user.role != 'DIRECTOR':
        return None
    return request.user

# DIRECTOR lkljasdgowaiejgoihjaewo;iglkasdhjgo;likahjo;gihjaewoipghoewjakhgjkeshgfjksd
@never_cache
def director_dashboard(request):
    """Director dashboard with campus-wide approval queue"""

    def get_director_user(request):
        user_id = request.session.get('user_id')
        if not user_id:
            return None
        try:
            user = User.objects.select_related('campus', 'college').get(
                id=user_id, role='DIRECTOR'
            )
            return user
        except User.DoesNotExist:
            return None

    user = get_director_user(request)
    if not user:
        messages.warning(request, 'Please login as Director')
        return redirect('accounts:login')

    today = timezone.now().date()
    current_month_start = today.replace(day=1)

    pending_travels = OfficialTravel.objects.filter(
        travel_order__status='PENDING_DIRECTOR'
    ).select_related(
        'travel_order',
        'travel_order__created_by',
        'travel_order__created_by__college',
        'itinerary',
        'itinerary__region_rate'
    ).order_by('-created_at')

    pending_count = pending_travels.count()

    approved_this_month = OfficialTravel.objects.filter(
        travel_order__approved_by_director=user,
        travel_order__director_approval_date__gte=current_month_start
    ).count()

    total_travels = OfficialTravel.objects.filter(
        travel_order__approved_by_director=user
    ).count()

    month_travels = OfficialTravel.objects.filter(
        travel_order__status='APPROVED',
        start_date__gte=current_month_start,
        start_date__lte=today
    ).select_related('itinerary')

    total_budget = sum(
        t.itinerary.estimated_total for t in month_travels if t.itinerary
    )
    total_budget_k = round(total_budget / 1000, 1)

    unread_count = Notification.objects.filter(
        user=user, is_read=False
    ).count()

    active_travelers = OfficialTravel.objects.filter(
        start_date__lte=today,
        end_date__gte=today,
        travel_order__status='APPROVED'
    ).values('participants_group__users').distinct().count()

    college_count = College.objects.count()

    approved_travels = TravelOrder.objects.filter(
        approved_by_director=user,
        director_approval_date__isnull=False,
        approved_by_dean__isnull=False,
        dean_approval_date__isnull=False
    )

    if approved_travels.exists():
        total_hours = 0
        count = 0
        for travel in approved_travels:
            delta = travel.director_approval_date - travel.dean_approval_date
            total_hours += delta.total_seconds() / 3600
            count += 1
        avg_approval_time = round(total_hours / count, 1)
    else:
        avg_approval_time = 0

    director_approved = TravelOrder.objects.filter(
    approved_by_director=user
    ).count()

    director_rejected = TravelOrder.objects.filter(
        status='REJECTED',
        director_remarks__isnull=False,  # Director left remarks when rejecting
        approved_by_dean__isnull=False   # Ensures it reached director stage
    ).count()

    total_decisions = director_approved + director_rejected

    approval_rate = round(
        (director_approved / total_decisions * 100)
        if total_decisions > 0 else 0,
        1
    )

    months = []
    month_labels = []
    month_data = []

    for i in range(5, -1, -1):
        month_date = today.replace(day=1) - timedelta(days=i * 30)
        month_start = month_date.replace(day=1)

        if month_date.month == 12:
            month_end = month_date.replace(
                year=month_date.year + 1,
                month=1,
                day=1
            )
        else:
            month_end = month_date.replace(
                month=month_date.month + 1,
                day=1
            )

        count = OfficialTravel.objects.filter(
            travel_order__approved_by_director=user,
            travel_order__director_approval_date__gte=month_start,
            travel_order__director_approval_date__lt=month_end
        ).count()

        month_labels.append(month_date.strftime('%b'))
        month_data.append(count)

    import calendar
    cal = calendar.monthcalendar(today.year, today.month)
    calendar_days = []

    month_travels_dates = OfficialTravel.objects.filter(
        Q(start_date__year=today.year, start_date__month=today.month) |
        Q(end_date__year=today.year, end_date__month=today.month)
    ).values_list('start_date', 'end_date')

    travel_days = set()

    for start, end in month_travels_dates:
        current = start
        while current <= end:
            if current.year == today.year and current.month == today.month:
                travel_days.add(current.day)
            current += timedelta(days=1)

    for week in cal:
        for day in week:
            if day == 0:
                calendar_days.append({
                    'day': '',
                    'has_travel': False,
                    'is_today': False
                })
            else:
                calendar_days.append({
                    'day': day,
                    'has_travel': day in travel_days,
                    'is_today': day == today.day
                })

    college_stats = OfficialTravel.objects.filter(
        travel_order__status='APPROVED',
        start_date__gte=current_month_start
    ).values(
        'travel_order__created_by__college__name'
    ).annotate(
        count=Count('id')
    ).order_by('-count')[:5]

    college_labels = [
        stat['travel_order__created_by__college__name'] or 'No College'
        for stat in college_stats
    ]
    college_data = [stat['count'] for stat in college_stats]

    context = {
        'title': 'Director Dashboard',
        'user': user,
        'pending_travels': pending_travels,
        'pending_count': pending_count,
        'approved_this_month': approved_this_month,
        'total_travels': total_travels,
        'total_budget': total_budget_k,
        'unread_count': unread_count,
        'active_travelers': active_travelers,
        'college_count': college_count,
        'avg_approval_time': avg_approval_time,
        'approval_rate': approval_rate,
        'month_labels': json.dumps(month_labels),
        'month_data': json.dumps(month_data),
        'current_month': today.strftime('%B %Y'),
        'calendar_days': calendar_days,
        'college_labels': json.dumps(college_labels),
        'college_data': json.dumps(college_data),
    }

    return render(
        request,
        'travel_app/director/director_dashboard.html',
        context
    )


@csrf_protect
@never_cache
def director_create_travel(request):
    """
    Director creates travel for themselves.
    Similar to dean_create_travel but for Director role.
    """
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to create a travel request')
        return redirect('accounts:login')

    if user.role != 'DIRECTOR':
        messages.error(request, 'Only Directors can access this page')
        return redirect('travel_app:director_dashboard')

    if request.method == 'POST':
        try:
            with transaction.atomic():
                # Step 1: Initiation Document
                doc_type = request.POST.get('doc_type')
                issuer = request.POST.get('issuer')
                date_issued = request.POST.get('date_issued')
                init_file = request.FILES.get('initiation_file')

                if not all([doc_type, issuer, date_issued, init_file]):
                    messages.error(request, 'Please fill in all initiation document fields')
                    return redirect('travel_app:director_create_travel')

                initiation_doc = TravelInitiationDocument.objects.create(
                    document_type=doc_type,
                    issuer=issuer,
                    date_issued=date_issued,
                    file=init_file,
                    uploaded_by=user
                )

                # Step 2: Travel Details
                start_date = request.POST.get('start_date')
                end_date = request.POST.get('end_date')
                destination = request.POST.get('destination')
                purpose = request.POST.get('purpose')
                is_out_of_province = request.POST.get('is_out_of_province') == 'on'
                initiating_office = request.POST.get('initiating_office', user.campus.name if user.campus else 'N/A')
                funding_office = request.POST.get('funding_office') or initiating_office
                prepayment_option = request.POST.get('prepayment_option')

                if not all([start_date, end_date, destination, purpose, prepayment_option]):
                    messages.error(request, 'Please fill in all required travel details')
                    return redirect('travel_app:director_create_travel')

                # Step 3: Files
                travel_order_file = request.FILES.get('travel_order_file')
                itinerary_file = request.FILES.get('itinerary_file')

                if not travel_order_file or not itinerary_file:
                    messages.error(request, 'Please upload all required documents')
                    return redirect('travel_app:director_create_travel')

                # Step 4: Budget Extraction
                from datetime import datetime as dt
                start = dt.strptime(start_date, '%Y-%m-%d').date()
                end = dt.strptime(end_date, '%Y-%m-%d').date()

                duration_days = (end - start).days + 1
                duration_nights = max(0, (end - start).days)

                region_rate = RegionRate.objects.filter(is_active=True).first()
                if not region_rate:
                    region_rate = RegionRate.objects.create(
                        region_code='VII',
                        region_name='Region 7',
                        meal_rate=180,
                        lodging_rate=900,
                        incidental_rate=180,
                        is_active=True
                    )

                # Try to extract budget
                extracted_budget = None
                try:
                    extracted_budget = extract_budget_from_file(itinerary_file)
                    if extracted_budget:
                        messages.success(request, f'✅ Budget extracted: ₱{extracted_budget:,.2f}')
                except:
                    pass

                if not extracted_budget:
                    extracted_budget = calculate_auto_budget(start, end, region_rate)

                itinerary = ItineraryOfTravel.objects.create(
                    region_rate=region_rate,
                    estimated_meals_count=duration_days * 3,
                    estimated_days=duration_days,
                    estimated_nights=duration_nights,
                    estimated_transportation=0,
                    estimated_other_expenses=0,
                    file=itinerary_file
                )

                # Adjust budget
                base_cost = (
                    (duration_days * 3 * region_rate.meal_rate) +
                    (duration_nights * region_rate.lodging_rate) +
                    (duration_days * region_rate.incidental_rate)
                )
                if extracted_budget > Decimal(str(base_cost)):
                    itinerary.estimated_other_expenses = int(extracted_budget - Decimal(str(base_cost)))
                    itinerary.save()

                # Step 5: Participant Group (Director only)
                participants_group = PayrollParticipants.objects.create()
                participants_group.users.add(user)

                # Step 6: Payroll
                payroll = None
                if prepayment_option == 'PREPAYMENT':
                    payroll = Payroll.objects.create(
                        itinerary=itinerary,
                        participants_group=participants_group
                    )

                # Step 7: Travel Order → PENDING_DIRECTOR (Director self-approves)
                travel_order = TravelOrder.objects.create(
                    initiation=initiation_doc,
                    created_by=user,
                    status='PENDING_DIRECTOR'
                )

                # Step 8: Letter Request if out-of-province
                letter_request = None
                if is_out_of_province:
                    justification = request.POST.get('justification', '')
                    letter_file = request.FILES.get('letter_file')

                    if not justification or not letter_file:
                        messages.error(request, 'Out-of-province travel requires justification and letter')
                        return redirect('travel_app:director_create_travel')

                    letter_request = LetterRequest.objects.create(
                        justification=justification,
                        travel_order=travel_order,
                        file=letter_file,
                        status='PENDING'
                    )

                # Step 9: Official Travel
                official_travel = OfficialTravel.objects.create(
                    start_date=start_date,
                    end_date=end_date,
                    destination=destination,
                    is_out_of_province=is_out_of_province,
                    purpose=purpose,
                    initiating_office=initiating_office,
                    funding_office=funding_office,
                    prepayment_option=prepayment_option,
                    travel_order=travel_order,
                    itinerary=itinerary,
                    letter_request=letter_request,
                    payroll=payroll,
                    participants_group=participants_group
                )

                # Notify director (confirmation)
                create_notification(
                    user,
                    'TRAVEL_CREATED',
                    'Travel Request Submitted',
                    f'Your travel to {destination} has been submitted. Approve it from your Pending Approvals.',
                    official_travel
                )

                messages.success(request, f'✅ Travel to {destination} created! Approve it from your dashboard.')
                return redirect('travel_app:travel_detail', travel_id=official_travel.id)

        except Exception as e:
            messages.error(request, f'Error: {str(e)}')
            return redirect('travel_app:director_create_travel')

    context = {
        'title': 'Create Travel Request - Director',
        'user': user,
    }
    return render(request, 'travel_app/director/director_create_travel.html', context)


# ==================== DIRECTOR TRAVEL HISTORY ====================

@never_cache
def director_travel_history(request):
    """Show all travels where Director is a participant"""
    user = get_authenticated_user(request)
    if not user or user.role != 'DIRECTOR':
        messages.warning(request, 'Please login as Director')
        return redirect('accounts:login')

    # Get all travels where Director is participant
    travels = OfficialTravel.objects.filter(
        participants_group__users=user
    ).select_related(
        'travel_order',
        'itinerary',
        'letter_request'
    ).prefetch_related(
        'participants_group__users',
        'post_travel_documents',
        'financial_documents'
    ).order_by('-created_at')

    # Filters
    status_filter = request.GET.get('status', '')
    year_filter = request.GET.get('year', '')
    search_query = request.GET.get('search', '')

    if status_filter:
        travels = travels.filter(travel_order__status=status_filter)
    if year_filter:
        travels = travels.filter(start_date__year=year_filter)
    if search_query:
        travels = travels.filter(
            Q(destination__icontains=search_query) |
            Q(purpose__icontains=search_query)
        )

    # Available years
    available_years = OfficialTravel.objects.filter(
        participants_group__users=user
    ).dates('start_date', 'year', order='DESC')

    # Annotate with status
    today = timezone.now().date()
    travels_with_status = []
    for travel in travels:
        is_completed = travel.end_date and travel.end_date < today
        is_ongoing = travel.start_date <= today <= travel.end_date if travel.end_date else False

        required_docs = ['COMPLETED', 'RECEIPTS', 'APPEARANCE', 'ACTUAL_ITINERARY']
        submitted_docs = travel.post_travel_documents.values_list('document_type', flat=True)
        missing_docs = [doc for doc in required_docs if doc not in submitted_docs]

        has_liquidation = hasattr(travel, 'liquidation_report')

        travels_with_status.append({
            'travel': travel,
            'is_completed': is_completed,
            'is_ongoing': is_ongoing,
            'missing_docs': missing_docs,
            'has_liquidation': has_liquidation,
            'needs_attention': (is_completed and (missing_docs or not has_liquidation))
        })

    context = {
        'title': 'My Travel History',
        'user': user,
        'travels_with_status': travels_with_status,
        'status_choices': TravelOrder.STATUS_CHOICES,
        'current_status': status_filter,
        'search_query': search_query,
        'available_years': available_years,
        'current_year': year_filter,
        'total_count': travels.count(),
        'completed_count': len([t for t in travels_with_status if t['is_completed']]),
    }

    return render(request, 'travel_app/director/director_travel_history.html', context)


# ==================== DIRECTOR NOTIFICATIONS (IMPROVED) ====================

@never_cache
def director_notifications(request):
    """Director notifications page - improved"""
    user = get_authenticated_user(request)
    if not user or user.role != 'DIRECTOR':
        return redirect('accounts:login')

    notifications = Notification.objects.filter(user=user).order_by('-created_at')

    # Count by category
    unread_count = notifications.filter(is_read=False).count()
    approval_count = notifications.filter(notification_type__icontains='APPROVAL').count()
    travel_count = notifications.filter(notification_type__icontains='TRAVEL').count()

    context = {
        'title': 'Notifications',
        'user': user,
        'notifications': notifications,
        'unread_count': unread_count,
        'approval_count': approval_count,
        'travel_count': travel_count,
    }
    return render(request, 'travel_app/director/director_notifications.html', context)


# ==================== DIRECTOR MARK ALL READ ====================

@csrf_protect
@never_cache
def director_mark_all_read(request):
    """Mark all notifications as read"""
    user = get_authenticated_user(request)
    if not user or user.role != 'DIRECTOR':
        return redirect('accounts:login')

    if request.method == 'POST':
        count = Notification.objects.filter(user=user, is_read=False).update(is_read=True)
        messages.success(request, f'Marked {count} notification(s) as read')

    return redirect('travel_app:director_notifications')


# ==================== DIRECTOR MARK SINGLE NOTIF READ ====================

@csrf_protect
@never_cache
def director_mark_notif_read(request, notif_id):
    """Mark a single notification as read"""
    user = get_authenticated_user(request)
    if not user or user.role != 'DIRECTOR':
        return redirect('accounts:login')

    try:
        notif = Notification.objects.get(id=notif_id, user=user)
        notif.is_read = True
        notif.save()
        messages.success(request, 'Notification marked as read')
    except Notification.DoesNotExist:
        messages.error(request, 'Notification not found')

    return redirect('travel_app:director_notifications')


# ==================== DIRECTOR APPROVE ====================

@csrf_protect
@never_cache
def director_approve(request, travel_id):
    """Approve a travel request"""
    user = get_authenticated_user(request)
    if not user or user.role != 'DIRECTOR':
        return redirect('accounts:login')
    
    if request.method != 'POST':
        return redirect('travel_app:director_dashboard')
    
    travel = get_object_or_404(OfficialTravel, id=travel_id)
    
    if travel.travel_order.status != 'PENDING_DIRECTOR':
        messages.error(request, 'This travel is not pending director approval')
        return redirect('travel_app:director_dashboard')
    
    # Approve
    travel.travel_order.status = 'APPROVED'
    travel.travel_order.approved_by_director = user
    travel.travel_order.director_approval_date = timezone.now()
    travel.travel_order.save()
    
    # Notify employee
    create_notification(
        travel.travel_order.created_by,
        'TRAVEL_APPROVED',
        'Travel Approved by Director',
        f'Your travel to {travel.destination} has been approved by Director {user.get_full_name()}',
        travel
    )
    
    messages.success(request, f'✅ Travel to {travel.destination} approved!')
    return redirect('travel_app:director_dashboard')


# ==================== DIRECTOR REJECT ====================

@csrf_protect
@never_cache
def director_reject(request, travel_id):
    """Reject a travel request"""
    user = get_authenticated_user(request)
    if not user or user.role != 'DIRECTOR':
        return redirect('accounts:login')
    
    if request.method != 'POST':
        return redirect('travel_app:director_dashboard')
    
    travel = get_object_or_404(OfficialTravel, id=travel_id)
    rejection_reason = request.POST.get('rejection_reason', '')
    
    if travel.travel_order.status != 'PENDING_DIRECTOR':
        messages.error(request, 'This travel is not pending director approval')
        return redirect('travel_app:director_dashboard')
    
    # Reject
    travel.travel_order.status = 'REJECTED'
    travel.travel_order.approved_by_director = user
    travel.travel_order.director_approval_date = timezone.now()
    travel.travel_order.director_remarks = rejection_reason
    travel.travel_order.rejection_reason = rejection_reason
    travel.travel_order.save()
    
    # Notify employee
    create_notification(
        travel.travel_order.created_by,
        'TRAVEL_REJECTED',
        'Travel Rejected by Director',
        f'Your travel to {travel.destination} has been rejected. Reason: {rejection_reason}',
        travel
    )
    
    messages.warning(request, f'❌ Travel to {travel.destination} rejected')
    return redirect('travel_app:director_dashboard')
