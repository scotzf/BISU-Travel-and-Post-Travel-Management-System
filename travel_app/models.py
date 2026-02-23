from django.db import models
from django.utils.timezone import now
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from accounts.models import User


# ==================== INITIATION & APPROVAL ====================

class TravelInitiationDocument(models.Model):
    """Memo or Request Letter that triggers travel"""
    DOC_TYPE_CHOICES = [
        ('MEMORANDUM', 'Memo'),
        ('LETTER', 'Request Letter')
    ]

    document_type = models.CharField(max_length=20, choices=DOC_TYPE_CHOICES)
    issuer = models.CharField(max_length=100, help_text="Who issued the memo/letter")
    date_issued = models.DateField(default=now, blank=True)

    file = models.FileField(
        upload_to='initiation_documents/%Y/%m/',
        help_text="Upload the actual memo or request letter"
    )

    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='initiated_documents'
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_document_type_display()} from {self.issuer} - {self.date_issued}"


class TravelOrder(models.Model):
    """Main travel order document with approval workflow"""
    STATUS_CHOICES = [
        ('PENDING_DEAN', 'Pending Dean Approval'),
        ('PENDING_DIRECTOR', 'Pending Director Approval'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    date_issued = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING_DEAN')
    is_active = models.BooleanField(default=True)

    initiation = models.ForeignKey(
        TravelInitiationDocument,
        on_delete=models.CASCADE,
        related_name='travel_orders'
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='created_travel_orders',
        help_text="Employee or Secretary who created this"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    # Dean approval
    approved_by_dean = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='dean_approved_orders'
    )
    dean_approval_date = models.DateTimeField(null=True, blank=True)
    dean_remarks = models.TextField(blank=True, max_length=500)

    # Director approval
    approved_by_director = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='director_approved_orders'
    )
    director_approval_date = models.DateTimeField(null=True, blank=True)
    director_remarks = models.TextField(blank=True, max_length=500)

    rejection_reason = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"TO #{self.id} - {self.status} - {self.date_issued}"

    def can_dean_approve(self):
        return self.status == 'PENDING_DEAN'

    def can_director_approve(self):
        return self.status == 'PENDING_DIRECTOR'


class TravelOrderComment(models.Model):
    """Comments/discussion on travel orders between Dean and Employee"""
    travel_order = models.ForeignKey(
        TravelOrder,
        on_delete=models.CASCADE,
        related_name='comments'
    )
    author = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='travel_comments'
    )
    message = models.TextField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    reply_to = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='replies'
    )

    def __str__(self):
        return f"Comment by {self.author.get_full_name()} on TO #{self.travel_order.id}"

    class Meta:
        ordering = ['created_at']


class LetterRequest(models.Model):
    """Letter to President for out-of-province travel approval"""
    PRESIDENT_STATUS_CHOICES = [
        ('PENDING', 'Pending President Approval'),
        ('APPROVED', 'Approved by President'),
        ('REJECTED', 'Rejected by President'),
    ]

    request_date = models.DateField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=PRESIDENT_STATUS_CHOICES, default='PENDING')
    justification = models.TextField(max_length=5000)

    file = models.FileField(
        upload_to='letter_requests/%Y/%m/',
        blank=True,
        null=True
    )

    travel_order = models.ForeignKey(
        TravelOrder,
        on_delete=models.CASCADE,
        related_name='president_letters'
    )

    approved_by_president = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='president_approved_letters'
    )
    approval_date = models.DateTimeField(null=True, blank=True)
    president_remarks = models.TextField(blank=True, max_length=500)

    def __str__(self):
        return f"Letter Request #{self.id} - {self.status}"


# ==================== BUDGET & RATES ====================

class RegionRate(models.Model):
    """Standard rates for different regions"""
    region_code = models.CharField(max_length=20, unique=True)
    region_name = models.CharField(max_length=100)

    meal_rate = models.IntegerField(validators=[MinValueValidator(0)], default=180)
    lodging_rate = models.IntegerField(validators=[MinValueValidator(0)], default=900)
    incidental_rate = models.IntegerField(validators=[MinValueValidator(0)], default=180)

    is_active = models.BooleanField(default=True)
    effective_date = models.DateField(default=now)

    def __str__(self):
        return f"{self.region_code} - {self.region_name}"

    class Meta:
        ordering = ['region_code']


class ItineraryOfTravel(models.Model):
    """Budget estimates for the travel"""
    region_rate = models.ForeignKey(RegionRate, on_delete=models.PROTECT)

    estimated_meals_count = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    estimated_days = models.IntegerField(validators=[MinValueValidator(1)])
    estimated_nights = models.IntegerField(validators=[MinValueValidator(0)])
    estimated_transportation = models.IntegerField(validators=[MinValueValidator(0)])
    estimated_other_expenses = models.IntegerField(validators=[MinValueValidator(0)])

    file = models.FileField(
        upload_to='itinerary_documents/%Y/%m/',
        blank=True,
        null=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def estimated_total(self):
        return (
            (self.estimated_meals_count * self.region_rate.meal_rate) +
            (self.estimated_nights * self.region_rate.lodging_rate) +
            (self.estimated_days * self.region_rate.incidental_rate) +
            self.estimated_transportation +
            self.estimated_other_expenses
        )

    def __str__(self):
        return f"Itinerary - ₱{self.estimated_total:,.2f} estimated"


# ==================== PARTICIPANTS & PAYROLL ====================

class PayrollParticipants(models.Model):
    """Group of people traveling together"""
    users = models.ManyToManyField(User, related_name='participant_groups')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        count = self.users.count()
        return f"Participant Group ({count} person{'s' if count != 1 else ''})"

    def get_participant_names(self):
        return ", ".join([user.get_full_name() for user in self.users.all()])


class Payroll(models.Model):
    """Payment calculation for group travel (prepayment)"""
    itinerary = models.ForeignKey(
        ItineraryOfTravel,
        on_delete=models.CASCADE,
        related_name='payrolls'
    )
    participants_group = models.ForeignKey(
        PayrollParticipants,
        on_delete=models.CASCADE,
        related_name='payrolls'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def total_count(self):
        return self.participants_group.users.count()

    @property
    def total_estimated_expenses(self):
        return self.itinerary.estimated_total * self.total_count

    def __str__(self):
        return f"Payroll - {self.total_count} person(s) × ₱{self.itinerary.estimated_total:,.2f}"


# ==================== OFFICIAL TRAVEL ====================

class OfficialTravel(models.Model):
    """The main travel record"""
    PREPAYMENT_OPTION = [
        ('PREPAYMENT', 'Prepayment'),
        ('NOT', 'No Prepayment')
    ]

    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    destination = models.CharField(max_length=200)
    is_out_of_province = models.BooleanField(default=False)
    purpose = models.TextField(max_length=5000)
    initiating_office = models.CharField(max_length=200)
    funding_office = models.CharField(max_length=200, blank=True)
    prepayment_option = models.CharField(max_length=13, choices=PREPAYMENT_OPTION)

    travel_order = models.ForeignKey(
        TravelOrder,
        on_delete=models.CASCADE,
        related_name='official_travels'
    )
    itinerary = models.ForeignKey(
        ItineraryOfTravel,
        on_delete=models.CASCADE,
        related_name='official_travels'
    )
    letter_request = models.ForeignKey(
        LetterRequest,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='official_travels'
    )
    payroll = models.ForeignKey(
        Payroll,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='official_travels'
    )
    participants_group = models.ForeignKey(
        PayrollParticipants,
        on_delete=models.CASCADE,
        related_name='official_travels'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.destination} ({self.start_date} to {self.end_date})"

    def get_duration_days(self):
        if self.end_date:
            return (self.end_date - self.start_date).days + 1
        return 1

    def is_participant(self, user):
        return self.participants_group.users.filter(id=user.id).exists()

    def get_status_display_custom(self):
        return self.travel_order.get_status_display()

    class Meta:
        ordering = ['-created_at']


# ==================== FINANCIAL DOCUMENTS ====================

class FinancialDocuments(models.Model):
    DOC_TYPE_CHOICES = [
        ('DV', 'Disbursement Voucher'),
        ('BURS', 'Budget Utilization Request and Status')
    ]

    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('PENDING', 'Pending Budget Officer Approval'),
        ('APPROVED', 'Approved by Budget Officer'),
        ('REJECTED', 'Rejected by Budget Officer'),
        ('REPLACED', 'Replaced by Newer Version'),
    ]

    document_type = models.CharField(max_length=50, choices=DOC_TYPE_CHOICES)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='ACTIVE')

    official_travel = models.ForeignKey(
        OfficialTravel,
        on_delete=models.CASCADE,
        related_name='financial_documents'
    )

    file = models.FileField(upload_to='financial_documents/%Y/%m/')

    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='uploaded_financial_docs'
    )
    upload_date = models.DateTimeField(auto_now_add=True)

    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_financial_docs'
    )
    approval_date = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, max_length=2000)

    def __str__(self):
        return f"{self.document_type} for Travel #{self.official_travel.id} ({self.status})"

    class Meta:
        ordering = ['-upload_date']


# ==================== POST-TRAVEL DOCUMENTS ====================

class PostTravelDocuments(models.Model):
    DOC_TYPE_CHOICES = [
        ('REPORT', 'Post Activity Report Form'),
        ('COMPLETED', 'Certificate of Travel Completed'),
        ('RECEIPTS', 'Certificate of Not Requiring Receipts'),
        ('APPEARANCE', 'Certificate of Appearance'),
        ('ACTUAL_ITINERARY', 'Actual Itinerary of Travel'),
    ]

    document_type = models.CharField(max_length=20, choices=DOC_TYPE_CHOICES)

    official_travel = models.ForeignKey(
        OfficialTravel,
        on_delete=models.CASCADE,
        related_name='post_travel_documents'
    )

    file = models.FileField(upload_to='post_travel_documents/%Y/%m/')

    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='uploaded_post_travel_docs'
    )
    submit_date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, max_length=2000)

    def __str__(self):
        return f"{self.get_document_type_display()} for Travel #{self.official_travel.id}"

    class Meta:
        ordering = ['-submit_date']


class LiquidationReport(models.Model):
    """Final settlement of travel expenses"""
    official_travel = models.OneToOneField(
        OfficialTravel,
        on_delete=models.CASCADE,
        related_name='liquidation_report'
    )

    total_amount_spent = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_advance = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_refunded = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_to_be_reimbursed = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    file = models.FileField(upload_to='liquidation_reports/%Y/%m/', blank=True, null=True)

    submitted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='submitted_liquidations'
    )
    submit_date = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, max_length=2000)

    def calculate_totals(self):
        if self.official_travel.prepayment_option == 'PREPAYMENT' and self.official_travel.payroll:
            self.cash_advance = self.official_travel.payroll.total_estimated_expenses
            difference = self.total_amount_spent - self.cash_advance
            if difference < 0:
                self.amount_refunded = abs(difference)
                self.amount_to_be_reimbursed = 0
            elif difference > 0:
                self.amount_refunded = 0
                self.amount_to_be_reimbursed = difference
            else:
                self.amount_refunded = 0
                self.amount_to_be_reimbursed = 0
        else:
            self.cash_advance = 0
            self.amount_refunded = 0
            self.amount_to_be_reimbursed = self.total_amount_spent
        self.save()

    def __str__(self):
        return f"Liquidation for Travel #{self.official_travel.id}"


# ==================== CHECK RELEASE ====================

class CheckRelease(models.Model):
    RELEASE_TYPE_CHOICES = [
        ('PREPAYMENT', 'Prepayment'),
        ('REIMBURSEMENT', 'Reimbursement'),
        ('REFUND_RETURN', 'Refund Return'),
    ]

    STATUS_CHOICES = [
        ('PENDING', 'Pending Release'),
        ('RELEASED', 'Check Released'),
        ('CLAIMED', 'Check Claimed by Employee'),
    ]

    official_travel = models.ForeignKey(
        OfficialTravel,
        on_delete=models.CASCADE,
        related_name='check_releases'
    )

    release_type = models.CharField(max_length=20, choices=RELEASE_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    check_number = models.CharField(max_length=50, blank=True)

    payee = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='received_checks'
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')

    prepared_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='prepared_checks'
    )
    release_date = models.DateTimeField(null=True, blank=True)
    claimed_date = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.get_release_type_display()} - ₱{self.amount:,.2f}"

    class Meta:
        ordering = ['-created_at']


# ==================== NOTIFICATIONS ====================

class Notification(models.Model):
    NOTIFICATION_TYPE_CHOICES = [
        ('TRAVEL_CREATED', 'Travel Created'),
        ('TRAVEL_APPROVED', 'Travel Approved'),
        ('TRAVEL_REJECTED', 'Travel Rejected'),
        ('DOCUMENTS_UPLOADED', 'Documents Uploaded'),
        ('FINANCIAL_APPROVED', 'Financial Documents Approved'),
        ('CHECK_READY', 'Check Ready for Pickup'),
        ('LIQUIDATION_REQUIRED', 'Liquidation Report Required'),
        ('APPROVAL_NEEDED', 'Approval Needed'),
        ('COMMENT_ADDED', 'Comment Added'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=30, choices=NOTIFICATION_TYPE_CHOICES)
    title = models.CharField(max_length=200)
    message = models.TextField(max_length=500)

    official_travel = models.ForeignKey(
        OfficialTravel,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='notifications'
    )

    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.notification_type} for {self.user.get_full_name()}"

    class Meta:
        ordering = ['-created_at']


# ==================== BUDGET ALLOCATION ====================

class BudgetAllocation(models.Model):
    """
    Budget allocation per fiscal year.
    Campus has its own total budget.
    Each College under a Campus has its own sub-budget.
    College budget is a child of Campus budget.
    """

    # Campus-level budget
    campus = models.ForeignKey(
        'accounts.Campus',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='budget_allocations'
    )

    # College-level budget (child of campus)
    college = models.ForeignKey(
        'accounts.College',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='budget_allocations'
    )

    fiscal_year = models.IntegerField(help_text="e.g., 2026")

    total_budget = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        help_text="Total allocated budget for the year"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def spent_budget(self):
        """Dynamically calculate from approved travels"""
        from django.db.models import Sum

        if self.college:
            result = OfficialTravel.objects.filter(
                travel_order__status='APPROVED',
                travel_order__created_by__college=self.college,
                start_date__year=self.fiscal_year
            ).aggregate(total=Sum('itinerary__estimated_other_expenses'))
            return result['total'] or 0

        if self.campus:
            result = OfficialTravel.objects.filter(
                travel_order__status='APPROVED',
                travel_order__created_by__campus=self.campus,
                start_date__year=self.fiscal_year
            ).aggregate(total=Sum('itinerary__estimated_other_expenses'))
            return result['total'] or 0

        return 0

    @property
    def remaining_budget(self):
        return self.total_budget - self.spent_budget

    @property
    def utilization_percentage(self):
        if self.total_budget == 0:
            return 0
        return round((self.spent_budget / self.total_budget) * 100, 1)

    def __str__(self):
        if self.college:
            return f"{self.college.name} Budget FY {self.fiscal_year} - ₱{self.total_budget:,.2f}"
        if self.campus:
            return f"{self.campus.name} Budget FY {self.fiscal_year} - ₱{self.total_budget:,.2f}"
        return f"Budget FY {self.fiscal_year}"

    class Meta:
        ordering = ['-fiscal_year']
        constraints = [
            models.UniqueConstraint(
                fields=['college', 'fiscal_year'],
                condition=models.Q(college__isnull=False),
                name='unique_college_fiscal_year'
            ),
            models.UniqueConstraint(
                fields=['campus', 'fiscal_year'],
                condition=models.Q(campus__isnull=False),
                name='unique_campus_fiscal_year'
            ),
        ]