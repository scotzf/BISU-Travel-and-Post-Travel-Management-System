from django.db import models
from django.utils.timezone import now


class Campus(models.Model):
    name = models.CharField(max_length=100, unique=True)
    street = models.CharField(max_length=100, null=True, blank=True)
    barangay = models.CharField(max_length=100, null=True, blank=True)
    municipality = models.CharField(max_length=100)
    province = models.CharField(max_length=100, default='Bohol')

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Campuses"


class College(models.Model):
    """Renamed from Department - A College belongs to a Campus"""
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=10, unique=True, blank=True)
    campus = models.ForeignKey(
        Campus,
        on_delete=models.CASCADE,
        related_name='colleges',
        null=True,
        blank=True
    )

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = ''.join(word[0] for word in self.name.split()).upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name_plural = "Colleges"


class User(models.Model):

    ROLE_CHOICES = [
        ('EMPLOYEE', 'Employee'),
        ('DEPT_SEC', 'Department Secretary'),
        ('CAMPUS_SEC', 'Campus Secretary'),
        ('DEAN', 'Dean'),
        ('DIRECTOR', 'Director'),
        ('PRESIDENT', 'President'),
        ('BUDGET', 'Budget Officer'),
        ('CASHIER', 'Cashier'),
        ('ADMIN', 'Administrator'),
    ]

    REGISTRATION_ROLE_CHOICES = [
        ('EMPLOYEE', 'Employee'),
        ('DEPT_SEC', 'Department Secretary'),
        ('CAMPUS_SEC', 'Campus Secretary'),
        ('DEAN', 'Dean'),
        ('DIRECTOR', 'Director'),
        ('PRESIDENT', 'President'),
        ('BUDGET', 'Budget Officer'),
        ('CASHIER', 'Cashier'),
    ]

    PREFERENCE_CHOICES = [
        ('PREPAYMENT', 'Prepayment'),
        ('NO_PREPAYMENT', 'No Prepayment'),
    ]

    username = models.CharField(max_length=200, unique=True)
    email = models.EmailField(unique=True)
    employee_id = models.CharField(max_length=20, unique=True, null=True, blank=True)

    first_name = models.CharField(max_length=50)
    last_name = models.CharField(max_length=50)
    middle_name = models.CharField(max_length=50, null=True, blank=True, default='N/A')

    password = models.CharField(max_length=128)

    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    # Changed from department to college
    college = models.ForeignKey(
        'College',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users'
    )

    campus = models.ForeignKey(
        'Campus',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='users'
    )

    preference = models.CharField(max_length=20, choices=PREFERENCE_CHOICES)
    phone_number = models.CharField(max_length=11, unique=True)
    on_travel = models.BooleanField(default=False)

    # Approval System Fields
    is_approved = models.BooleanField(
        default=False,
        help_text="Admin must approve all new accounts"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Account can be deactivated"
    )
    approved_by = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_users',
        help_text="Admin who approved this account"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.username

    def can_login(self):
        """Check if user can login"""
        if not self.is_active:
            return False, "Your account has been deactivated. Please contact the administrator."
        if self.role == 'ADMIN':
            return True, None
        if not self.is_approved:
            return False, "Your account is pending approval. Please wait for administrator confirmation."
        return True, None

    def get_full_name(self):
        if self.middle_name and self.middle_name != 'N/A':
            return f"{self.first_name} {self.middle_name} {self.last_name}"
        return f"{self.first_name} {self.last_name}"