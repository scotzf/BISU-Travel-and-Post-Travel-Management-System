from django.db import models
from django.utils.timezone import now
from django.core.validators import MinValueValidator


# ══════════════════════════════════════════════════════════════════════
# BUDGET SOURCES
# ══════════════════════════════════════════════════════════════════════

class BudgetSource(models.Model):
    """
    A named budget category for a given fiscal year.

    COLLEGE scope → college_budget_amount is given to EVERY college.
    CAMPUS scope  → campus_budget_amount is a single campus-wide pool.

    Admin creates these. Secretaries pick from them when tagging a travel.
    """
    SCOPE_CHOICES = [
        ('COLLEGE', 'College-Level'),
        ('CAMPUS',  'Campus-Level'),
    ]

    name                  = models.CharField(max_length=100)
    scope                 = models.CharField(max_length=10, choices=SCOPE_CHOICES, default='COLLEGE')
    year                  = models.IntegerField(help_text='Fiscal year, e.g. 2026')
    college_budget_amount = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Amount allocated to EACH college for this source/year'
    )
    campus_budget_amount  = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Total campus-wide pool for this source/year'
    )
    description = models.TextField(blank=True, max_length=500)
    is_active   = models.BooleanField(default=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.year}) — {self.get_scope_display()}"

    def get_budget_amount(self):
        return self.college_budget_amount if self.scope == 'COLLEGE' else self.campus_budget_amount

    def get_or_create_college_usage(self, college):
        if self.scope != 'COLLEGE':
            raise ValueError("This source is CAMPUS-scoped. Use get_or_create_campus_usage().")
        return BudgetUsage.objects.get_or_create(
            college=college, budget_source=self, year=self.year,
            defaults={'allocated_amount': self.college_budget_amount}
        )

    def get_or_create_campus_usage(self, campus):
        if self.scope != 'CAMPUS':
            raise ValueError("This source is COLLEGE-scoped. Use get_or_create_college_usage().")
        return CampusBudgetUsage.objects.get_or_create(
            campus=campus, budget_source=self, year=self.year,
            defaults={'allocated_amount': self.campus_budget_amount}
        )

    class Meta:
        ordering = ['-year', 'name']
        unique_together = [['name', 'year', 'scope']]
        verbose_name = 'Budget Source'
        verbose_name_plural = 'Budget Sources'


class BudgetUsage(models.Model):
    """
    Tracks spending per college per budget source per year.
    One row auto-created the first time a college uses a source.
    """
    college          = models.ForeignKey('accounts.College', on_delete=models.CASCADE, related_name='budget_usage')
    budget_source    = models.ForeignKey(BudgetSource, on_delete=models.CASCADE, related_name='college_usage')
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
        return f"{self.college.name} | {self.budget_source.name} ({self.year})"

    class Meta:
        unique_together = [['college', 'budget_source', 'year']]
        ordering = ['-year', 'college__name']
        verbose_name = 'College Budget Usage'
        verbose_name_plural = 'College Budget Usage Records'


class CampusBudgetUsage(models.Model):
    """
    Tracks spending per campus per budget source per year.
    Used for CAMPUS-scoped sources (cross-college travel).
    """
    campus           = models.ForeignKey('accounts.Campus', on_delete=models.CASCADE, related_name='budget_usage')
    budget_source    = models.ForeignKey(BudgetSource, on_delete=models.CASCADE, related_name='campus_usage')
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
        return f"{self.campus.name} | {self.budget_source.name} ({self.year})"

    class Meta:
        unique_together = [['campus', 'budget_source', 'year']]
        ordering = ['-year', 'campus__name']
        verbose_name = 'Campus Budget Usage'
        verbose_name_plural = 'Campus Budget Usage Records'


# ══════════════════════════════════════════════════════════════════════
# EVENT GROUP
# ══════════════════════════════════════════════════════════════════════

class EventGroup(models.Model):
    """
    Links multiple travel records that belong to the same event
    (e.g. same conference attended by people from different colleges).

    Created manually by a secretary when they spot a duplicate,
    or suggested automatically by the duplicate detector.

    Having this lets the stats engine answer:
    "How much did the whole campus spend on this one event?"
    """
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
# TRAVEL RECORD  (the central model — replaces OfficialTravel + TravelOrder)
# ══════════════════════════════════════════════════════════════════════

class TravelRecord(models.Model):
    """
    One travel = one document folder.
    No approval workflow — this is a permanent archive / budget tracker.

    SCOPE is auto-detected from participants:
      COLLEGE → all participants share the same college
      CAMPUS  → participants come from multiple colleges

    Scope determines which secretary queue this appears in:
      COLLEGE → Dept Secretary of that college
      CAMPUS  → Campus Secretary
    """
    SCOPE_CHOICES = [
        ('COLLEGE', 'College-Level'),
        ('CAMPUS',  'Campus-Level'),
    ]

    # ── Core travel info ──────────────────────────────────────────────
    destination      = models.CharField(max_length=200)
    start_date       = models.DateField()
    end_date         = models.DateField(null=True, blank=True)
    purpose          = models.TextField(max_length=2000)
    is_out_of_province = models.BooleanField(default=False)

    # ── Scope (auto-set on save, can be overridden) ───────────────────
    scope = models.CharField(
        max_length=10, choices=SCOPE_CHOICES, default='COLLEGE',
        help_text='Auto-detected from participants. COLLEGE = same college, CAMPUS = cross-college.'
    )

    # ── Who created it ────────────────────────────────────────────────
    created_by = models.ForeignKey(
        'accounts.User', on_delete=models.CASCADE,
        related_name='created_travels'
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    # ── Budget ────────────────────────────────────────────────────────
    budget_source = models.ForeignKey(
        BudgetSource, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='travel_records',
        help_text='Assigned by Secretary after travel is created.'
    )
    amount_deducted = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Snapshot of amount deducted from budget source. Preserved even if source changes.'
    )
    budget_tagged_by = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='budget_tagged_travels',
        help_text='Secretary who assigned the budget source.'
    )
    budget_tagged_at = models.DateTimeField(null=True, blank=True)

    # ── Event grouping (duplicate detection) ─────────────────────────
    event_group = models.ForeignKey(
        EventGroup, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='travel_records',
        help_text='Links this travel to others that are part of the same event.'
    )
    funding_college = models.ForeignKey(
    'accounts.College',
    on_delete=models.SET_NULL,
    null=True, blank=True,
    related_name='funded_travels',
    help_text='Set by Campus Secretary when cross-college travel is funded by a specific college'
    )

    # ── Notes ─────────────────────────────────────────────────────────
    notes = models.TextField(blank=True, max_length=1000)

    # ── Helpers ───────────────────────────────────────────────────────
    def get_duration_days(self):
        if self.end_date:
            return (self.end_date - self.start_date).days + 1
        return 1

    def detect_scope(self):
        """
        Re-evaluate scope from current participants.
        Call this after adding/removing participants.
        Returns 'COLLEGE' or 'CAMPUS'.
        """
        colleges = set(
            self.participants.exclude(college_snapshot='')
                             .values_list('college_snapshot', flat=True)
        )
        return 'CAMPUS' if len(colleges) > 1 else 'COLLEGE'

    def refresh_scope(self):
        self.scope = self.detect_scope()
        self.save(update_fields=['scope'])

    @property
    def document_count(self):
        return self.documents.count()

    @property
    def document_types_uploaded(self):
        return set(self.documents.values_list('doc_type', flat=True))

    @property
    def missing_documents(self):
        return [t for t, _ in TravelDocument.DOC_TYPE_CHOICES
                if t not in self.document_types_uploaded]

    @property
    def completeness_percentage(self):
        total = len(TravelDocument.DOC_TYPE_CHOICES)
        uploaded = len(self.document_types_uploaded)
        return round((uploaded / total) * 100) if total else 0

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

    college_snapshot stores the participant's college name AT THE TIME
    of travel creation. This preserves historical accuracy even if the
    user's college assignment changes later.

    This snapshot is also what scope detection reads — it counts distinct
    college values to decide COLLEGE vs CAMPUS scope.
    """
    travel_record     = models.ForeignKey(TravelRecord, on_delete=models.CASCADE, related_name='participants')
    user              = models.ForeignKey('accounts.User', on_delete=models.CASCADE, related_name='travel_participations')
    college_snapshot  = models.CharField(
        max_length=100, blank=True,
        help_text='College name at time of travel. Used for scope detection and historical stats.'
    )
    campus_snapshot   = models.CharField(
        max_length=100, blank=True,
        help_text='Campus name at time of travel.'
    )
    added_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # Auto-snapshot college and campus on first save
        if not self.college_snapshot and self.user.college:
            self.college_snapshot = self.user.college.name
        if not self.campus_snapshot and self.user.campus:
            self.campus_snapshot = self.user.campus.name
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.get_full_name()} → {self.travel_record}"

    class Meta:
        unique_together = [['travel_record', 'user']]
        ordering = ['added_at']
        verbose_name = 'Travel Participant'
        verbose_name_plural = 'Travel Participants'


# ══════════════════════════════════════════════════════════════════════
# TRAVEL DOCUMENTS  (the document folder contents)
# ══════════════════════════════════════════════════════════════════════

class TravelDocument(models.Model):
    """
    A single uploaded file attached to a TravelRecord.

    When a document is uploaded, the system passes it to Ollama for
    extraction. Extracted fields are stored alongside the file.
    Secretary reviews and confirms — setting is_confirmed=True locks
    the extracted data into the stats engine.

    One travel can have multiple documents of the same type
    (e.g. updated versions of a DV). The latest confirmed version
    of each type is used for stats.
    """
    DOC_TYPE_CHOICES = [
        ('TRAVEL_ORDER',      'Travel Order'),
        ('ITINERARY',         'Itinerary of Travel'),
        ('DV',                'Disbursement Voucher'),
        ('BURS',              'Budget Utilization Request and Status'),
        ('RECEIPTS',          'Official Receipts'),
        ('CERTIFICATE',       'Certificate of Appearance / Completion'),
        ('POST_REPORT',       'Post-Activity Report'),
        ('LETTER_REQUEST',    'Letter Request (Out-of-Province)'),
    ]

    travel_record = models.ForeignKey(TravelRecord, on_delete=models.CASCADE, related_name='documents')
    doc_type      = models.CharField(max_length=30, choices=DOC_TYPE_CHOICES)
    file          = models.FileField(upload_to='travel_documents/%Y/%m/')
    uploaded_by   = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, related_name='uploaded_travel_docs'
    )
    uploaded_at   = models.DateTimeField(auto_now_add=True)
    notes         = models.TextField(blank=True, max_length=500)

    # ── AI-extracted fields (populated by Ollama on upload) ───────────
    extracted_destination = models.CharField(max_length=200, blank=True)
    extracted_start_date  = models.DateField(null=True, blank=True)
    extracted_end_date    = models.DateField(null=True, blank=True)
    extracted_amount      = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    extracted_purpose     = models.TextField(blank=True, max_length=500)
    extracted_num_travelers = models.IntegerField(null=True, blank=True)
    extraction_raw        = models.TextField(
        blank=True,
        help_text='Raw JSON response from Ollama for debugging.'
    )
    extraction_attempted  = models.BooleanField(default=False)
    extraction_successful = models.BooleanField(default=False)

    # ── Secretary review ──────────────────────────────────────────────
    is_confirmed    = models.BooleanField(
        default=False,
        help_text='Secretary confirmed extracted data is correct. Used by stats engine.'
    )
    confirmed_by    = models.ForeignKey(
        'accounts.User', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='confirmed_travel_docs'
    )
    confirmed_at    = models.DateTimeField(null=True, blank=True)
    # Add to TravelDocument
    detected_doc_type = models.CharField(max_length=30, blank=True,
        help_text='Document type as detected by AI (may differ from user selection)')
    extraction_confidence = models.CharField(max_length=10, blank=True,
        help_text='high / medium / low')
    extraction_status = models.CharField(max_length=20, default='pending',
        help_text='pending / processing / done / failed')

    def __str__(self):
        return f"{self.get_doc_type_display()} — {self.travel_record}"

    class Meta:
        ordering = ['-uploaded_at']
        verbose_name = 'Travel Document'
        verbose_name_plural = 'Travel Documents'


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