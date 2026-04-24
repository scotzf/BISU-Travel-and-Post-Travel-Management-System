from django.db import models
from django.utils.timezone import now
from django.core.validators import MinValueValidator


# ══════════════════════════════════════════════════════════════════════
# BUDGET SOURCES
# ══════════════════════════════════════════════════════════════════════

class BudgetSource(models.Model):
    SCOPE_CHOICES = [
        ('COLLEGE', 'College-Level'),
        ('CAMPUS',  'Campus-Level'),
    ]

    budget_name   = models.CharField(max_length=100)
    budget_scope  = models.CharField(max_length=10, choices=SCOPE_CHOICES, default='COLLEGE')
    fiscal_year   = models.IntegerField(help_text='Fiscal year, e.g. 2026')
    budget_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Amount allocated per college (COLLEGE scope) or total campus pool (CAMPUS scope)'
    )
    description = models.TextField(blank=True, max_length=500)
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.budget_name} ({self.fiscal_year}) — {self.get_budget_scope_display()}"

    def get_or_create_usage(self, user):
        """
        Get or create a BudgetUsage row for this user and source.

        Works for both COLLEGE and CAMPUS scoped sources.
        """
        return BudgetUsage.objects.get_or_create(
            user=user,
            budget_source=self,
            year=self.fiscal_year,
            defaults={'allocated_amount': self.budget_amount}
        )

    class Meta:
        ordering = ['-fiscal_year', 'budget_name']
        unique_together = [['budget_name', 'fiscal_year', 'budget_scope']]
        verbose_name = 'Budget Source'
        verbose_name_plural = 'Budget Sources'


# ══════════════════════════════════════════════════════════════════════
# BUDGET USAGE  (merged — replaces BudgetUsage + CampusBudgetUsage)
# ══════════════════════════════════════════════════════════════════════

class BudgetUsage(models.Model):
    """
    Tracks spending per user per budget source per year.

    - user.college  → tells us which college this belongs to
    - user.campus   → tells us which campus this belongs to
    - budget_source.budget_scope → tells us if it's COLLEGE or CAMPUS level

    This replaces both the old BudgetUsage and CampusBudgetUsage models.
    Allows answering: "How much did employee X spend this year?"
    Secretaries can aggregate by user__college or user__campus.
    """
    user = models.ForeignKey(
        'accounts.User',
        on_delete=models.CASCADE,
        related_name='budget_usage',
        null=True,
        blank=True,
    )
    budget_source    = models.ForeignKey(BudgetSource, on_delete=models.CASCADE, related_name='usage')
    year             = models.IntegerField()
    allocated_amount = models.DecimalField(max_digits=12, decimal_places=2)
    used_amount      = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    @property
    def remaining_amount(self):
        return self.allocated_amount - self.used_amount

    @property
    def usage_percentage(self):
        if self.allocated_amount > 0:
            return round((self.used_amount / self.allocated_amount) * 100, 1)
        return 0

    @property
    def status(self):
        pct = self.usage_percentage
        if pct >= 100: return 'exhausted'
        if pct >= 80:  return 'critical'
        if pct >= 60:  return 'warning'
        return 'healthy'

    def deduct(self, amount):
        from decimal import Decimal
        self.used_amount += Decimal(str(amount))
        self.save(update_fields=['used_amount'])
        return self.used_amount <= self.allocated_amount

    def restore(self, amount):
        from decimal import Decimal
        self.used_amount = max(Decimal('0'), self.used_amount - Decimal(str(amount)))
        self.save(update_fields=['used_amount'])

    def __str__(self):
        return f"{self.user.get_full_name()} | {self.budget_source.budget_name} ({self.year})"

    class Meta:
        unique_together = [['user', 'budget_source', 'year']]
        ordering = ['-year', 'user__last_name']
        verbose_name = 'Budget Usage'
        verbose_name_plural = 'Budget Usage Records'


# ══════════════════════════════════════════════════════════════════════
# EVENT GROUP
# ══════════════════════════════════════════════════════════════════════

class EventGroup(models.Model):
    SCOPE_CHOICES = [
        ('COLLEGE', 'Single College'),
        ('CAMPUS',  'Cross-College / Campus-Wide'),
    ]

    name        = models.CharField(max_length=200, help_text='e.g. "CHED Regional Conference 2026"')
    destination = models.CharField(max_length=200)
    start_date  = models.DateField()
    end_date    = models.DateField(null=True, blank=True)
    scope       = models.CharField(max_length=10, choices=SCOPE_CHOICES, default='COLLEGE')
    notes       = models.TextField(blank=True, max_length=500)
    created_by  = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL, null=True,
        related_name='created_event_groups'
    )
    created_at  = models.DateTimeField(auto_now_add=True)

    @property
    def total_participants(self):
        return TravelParticipant.objects.filter(travel_record__event_group=self).count()

    @property
    def total_amount_deducted(self):
        from django.db.models import Sum
        result = self.travel_records.aggregate(total=Sum('amount_deducted'))
        return result['total'] or 0

    def __str__(self):
        return f"{self.name} ({self.start_date})"

    class Meta:
        ordering = ['-start_date']
        verbose_name = 'Event Group'
        verbose_name_plural = 'Event Groups'


# ══════════════════════════════════════════════════════════════════════
# TRAVEL RECORD
# ══════════════════════════════════════════════════════════════════════

class TravelRecord(models.Model):
    SCOPE_CHOICES = [
        ('COLLEGE', 'College-Level'),
        ('CAMPUS',  'Campus-Level'),
    ]

    destination        = models.CharField(max_length=200)
    start_date         = models.DateField()
    end_date           = models.DateField(null=True, blank=True)
    purpose            = models.TextField(max_length=2000)
    is_out_of_province = models.BooleanField(default=False)

    scope = models.CharField(
        max_length=10, choices=SCOPE_CHOICES, default='COLLEGE',
        help_text='Auto-detected from participants. COLLEGE = same college, CAMPUS = cross-college.'
    )

    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.CASCADE,
        related_name='created_travels'
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    budget_source = models.ForeignKey(
        BudgetSource, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='travel_records',
    )
    amount_deducted = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
    )
    budget_tagged_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='budget_tagged_travels',
    )
    budget_tagged_at = models.DateTimeField(null=True, blank=True)

    event_group = models.ForeignKey(
        EventGroup, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='travel_records',
    )
    funding_college = models.ForeignKey(
        'accounts.College',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='funded_travels',
    )

    notes = models.TextField(blank=True, max_length=1000)

    def get_duration_days(self):
        if self.end_date:
            return (self.end_date - self.start_date).days + 1
        return 1

    def detect_scope(self):
        colleges = set(
            self.participants.exclude(college_name='')
                             .values_list('college_name', flat=True)
        )
        return 'CAMPUS' if len(colleges) > 1 else 'COLLEGE'

    def refresh_scope(self):
        self.scope = self.detect_scope()
        self.save(update_fields=['scope'])

    @property
    def document_count(self):
        return ParticipantDocument.objects.filter(
            participant__travel_record=self
        ).count()

    @property
    def completeness_percentage(self):
        participants = self.participants.count()
        if not participants:
            return 0
        total_possible = participants * len(ParticipantDocument.DOC_TYPE_CHOICES)
        uploaded = ParticipantDocument.objects.filter(
            participant__travel_record=self
        ).count()
        return round((uploaded / total_possible) * 100) if total_possible else 0

    @property
    def is_budget_tagged(self):
        return self.budget_source is not None

    @property
    def participant_count(self):
        return self.participants.count()

    def __str__(self):
        return f"{self.destination} | {self.start_date} | {self.created_by.get_full_name()}"

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Travel Record'
        verbose_name_plural = 'Travel Records'


# ══════════════════════════════════════════════════════════════════════
# TRAVEL PARTICIPANTS
# ══════════════════════════════════════════════════════════════════════

class TravelParticipant(models.Model):
    """
    One row per person per travel.

    college_name and campus_name store the participant's college/campus
    AT THE TIME of travel creation for historical accuracy.
    """
    travel_record = models.ForeignKey(TravelRecord, on_delete=models.CASCADE, related_name='participants')
    user          = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='travel_participations')
    college_name  = models.CharField(
        max_length=100, blank=True,
        help_text='College name at time of travel. Used for scope detection and historical stats.'
    )
    campus_name   = models.CharField(
        max_length=100, blank=True,
        help_text='Campus name at time of travel.'
    )
    added_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.college_name and self.user.college:
            self.college_name = self.user.college.name
        if not self.campus_name and self.user.campus:
            self.campus_name = self.user.campus.name
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.get_full_name()} → {self.travel_record}"

    class Meta:
        unique_together = [['travel_record', 'user']]
        ordering = ['added_at']
        verbose_name = 'Travel Participant'
        verbose_name_plural = 'Travel Participants'


# ══════════════════════════════════════════════════════════════════════
# PARTICIPANT DOCUMENT
# ══════════════════════════════════════════════════════════════════════

class ParticipantDocument(models.Model):
    DOC_TYPE_CHOICES = [
        ('TRAVEL_ORDER',   'Travel Order'),
        ('ITINERARY',      'Itinerary of Travel'),
        ('DV',             'Disbursement Voucher'),
        ('BURS',           'Budget Utilization Request and Status'),
        ('RECEIPTS',       'Official Receipts'),
        ('CERTIFICATE',    'Certificate of Appearance / Completion'),
        ('POST_REPORT',    'Post-Activity Report'),
        ('LETTER_REQUEST', 'Letter Request (Out-of-Province)'),
    ]

    participant   = models.ForeignKey(
        TravelParticipant, on_delete=models.CASCADE,
        related_name='documents'
    )
    doc_type      = models.CharField(max_length=30, choices=DOC_TYPE_CHOICES)
    file          = models.FileField(upload_to='participant_documents/%Y/%m/')
    uploaded_by   = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, related_name='uploaded_participant_docs'
    )
    uploaded_at   = models.DateTimeField(auto_now_add=True)
    notes         = models.TextField(blank=True, max_length=500)

    extracted_destination = models.CharField(max_length=200, blank=True)
    extracted_start_date  = models.DateField(null=True, blank=True)
    extracted_end_date    = models.DateField(null=True, blank=True)
    extracted_purpose     = models.TextField(blank=True, max_length=500)
    extracted_amount      = models.DecimalField(
        max_digits=12, decimal_places=2,
        null=True, blank=True,
        help_text='Amount extracted from Itinerary. Used for per-person expense tracking.'
    )
    extraction_attempted  = models.BooleanField(default=False)
    extraction_successful = models.BooleanField(default=False)
    extraction_raw        = models.TextField(blank=True)

    is_confirmed  = models.BooleanField(default=False)
    confirmed_by  = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='confirmed_participant_docs'
    )
    confirmed_at  = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.get_doc_type_display()} — {self.participant}"

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'Participant Document'
        verbose_name_plural = 'Participant Documents'


# ══════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════

class Notification(models.Model):
    NOTIFICATION_TYPE_CHOICES = [
        ('TRAVEL_CREATED',      'Travel Created'),
        ('BUDGET_TAGGED',       'Budget Source Tagged'),
        ('DOCUMENT_UPLOADED',   'Document Uploaded'),
        ('BUDGET_LOW',          'Budget Running Low'),
        ('BUDGET_EXHAUSTED',    'Budget Exhausted'),
        ('DUPLICATE_DETECTED',  'Possible Duplicate Travel Detected'),
        ('EXTRACTION_DONE',     'AI Extraction Complete — Please Review'),
        ('OVER_BUDGET',         'Travel Exceeds Budget'),
    ]

    user              = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=30, choices=NOTIFICATION_TYPE_CHOICES)
    title             = models.CharField(max_length=200)
    message           = models.TextField(max_length=500)
    travel_record     = models.ForeignKey(
        TravelRecord, on_delete=models.CASCADE,
        null=True, blank=True, related_name='notifications'
    )
    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.notification_type} → {self.user.get_full_name()}"

    class Meta:
        ordering = ['-created_at']