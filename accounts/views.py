from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.hashers import make_password, check_password
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.cache import never_cache
from django.utils import timezone
from django import forms
import re
from .models import User, College, Campus


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def get_authenticated_user(request):
    """
    Returns the User object for the current session, or None.
    Use this in every travel_app view instead of repeating session logic.

    Usage:
        user = get_authenticated_user(request)
        if not user:
            return redirect('accounts:login')
    """
    user_id = request.session.get('user_id')
    if not user_id:
        return None
    try:
        return User.objects.select_related('college', 'campus').get(id=user_id)
    except User.DoesNotExist:
        request.session.flush()
        return None


# ══════════════════════════════════════════════════════════════════════
# FORMS
# ══════════════════════════════════════════════════════════════════════

class LoginForm(forms.Form):
    username = forms.CharField(
        max_length=200,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your email',
            'autocomplete': 'username'
        }),
        error_messages={'required': 'Email address is required'}
    )
    password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your password',
            'autocomplete': 'current-password'
        }),
        error_messages={'required': 'Password is required'}
    )
    remember_me = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip().lower()
        if not username:
            raise forms.ValidationError('Email address cannot be empty')
        return username

    def clean_password(self):
        password = self.cleaned_data.get('password', '')
        if not password:
            raise forms.ValidationError('Password cannot be empty')
        if len(password) < 8:
            raise forms.ValidationError('Invalid credentials')
        return password


# Registration only allows these 3 roles — Admin is created via manage.py
REGISTRATION_ROLE_CHOICES = [
    ('EMPLOYEE',   'Employee'),
    ('DEPT_SEC',   'Department Secretary'),
    ('CAMPUS_SEC', 'Campus Secretary'),
]


class RegisterForm(forms.Form):
    first_name = forms.CharField(
        max_length=50, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'First Name'}),
        error_messages={'required': 'First name is required'}
    )
    middle_name = forms.CharField(
        max_length=50, required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Middle Name (Optional)'})
    )
    last_name = forms.CharField(
        max_length=50, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Last Name'}),
        error_messages={'required': 'Last name is required'}
    )
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'example@bisu.edu.ph',
            'autocomplete': 'email'
        }),
        error_messages={'required': 'BISU email address is required', 'invalid': 'Enter a valid email address'}
    )
    phone_number = forms.CharField(
        max_length=11, required=True,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': '09XXXXXXXXX', 'maxlength': '11'}),
        error_messages={'required': 'Phone number is required'}
    )
    employee_id = forms.CharField(
        max_length=20, required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Employee ID (Optional)'})
    )
    password = forms.CharField(
        min_length=8, required=True,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Create a strong password',
            'autocomplete': 'new-password'
        }),
        error_messages={'required': 'Password is required', 'min_length': 'Password must be at least 8 characters'}
    )
    confirm_password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirm your password',
            'autocomplete': 'new-password'
        }),
        error_messages={'required': 'Password confirmation is required'}
    )

    # Only 3 roles available on registration
    role = forms.ChoiceField(
        choices=REGISTRATION_ROLE_CHOICES,
        required=True,
        widget=forms.Select(attrs={'class': 'form-control', 'id': 'id_role'}),
        error_messages={'required': 'Please select a role'}
    )
    campus = forms.ModelChoiceField(
        queryset=Campus.objects.all(),
        required=True,
        widget=forms.Select(attrs={'class': 'form-control'}),
        empty_label='-- Select Campus --',
        error_messages={'required': 'Campus selection is required'}
    )
    college = forms.ModelChoiceField(
        queryset=College.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-control', 'id': 'id_college'}),
        empty_label='-- Select College (Optional) --'
    )

    # ── Field validators (unchanged from original) ────────────────────

    def clean_first_name(self):
        first_name = self.cleaned_data.get('first_name', '').strip()
        if not re.match(r"^[a-zA-Z\s\-']+$", first_name):
            raise forms.ValidationError('First name contains invalid characters')
        return first_name.title()

    def clean_middle_name(self):
        middle_name = self.cleaned_data.get('middle_name', '').strip()
        if middle_name:
            if not re.match(r"^[a-zA-Z\s\-']+$", middle_name):
                raise forms.ValidationError('Middle name contains invalid characters')
            return middle_name.title()
        return None

    def clean_last_name(self):
        last_name = self.cleaned_data.get('last_name', '').strip()
        if not re.match(r"^[a-zA-Z\s\-']+$", last_name):
            raise forms.ValidationError('Last name contains invalid characters')
        return last_name.title()

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        try:
            validate_email(email)
        except ValidationError:
            raise forms.ValidationError('Enter a valid email address')
        if not email.endswith('@bisu.edu.ph'):
            raise forms.ValidationError('Only official BISU email addresses (@bisu.edu.ph) are allowed')
        if len(email.split('@')[0]) < 3:
            raise forms.ValidationError('Email address is too short')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('This email is already registered.')
        return email

    def clean_phone_number(self):
        phone = ''.join(filter(str.isdigit, self.cleaned_data.get('phone_number', '')))
        if len(phone) != 11:
            raise forms.ValidationError('Phone number must be exactly 11 digits')
        if not phone.startswith('09'):
            raise forms.ValidationError('Phone number must start with 09')
        if User.objects.filter(phone_number=phone).exists():
            raise forms.ValidationError('This phone number is already registered')
        return phone

    def clean_employee_id(self):
        employee_id = self.cleaned_data.get('employee_id', '').strip()
        if employee_id:
            employee_id = employee_id.replace(' ', '').upper()
            if not re.match(r'^[A-Z0-9\-]+$', employee_id):
                raise forms.ValidationError('Employee ID can only contain letters, numbers, and hyphens')
            if len(employee_id) < 3 or len(employee_id) > 20:
                raise forms.ValidationError('Employee ID must be between 3 and 20 characters')
            if User.objects.filter(employee_id=employee_id).exists():
                raise forms.ValidationError('This employee ID is already registered')
            return employee_id
        return None

    def clean_password(self):
        password = self.cleaned_data.get('password', '')
        if len(password) < 8:
            raise forms.ValidationError('Password must be at least 8 characters long')
        if not re.search(r'[A-Z]', password):
            raise forms.ValidationError('Password must contain at least one uppercase letter')
        if not re.search(r'[a-z]', password):
            raise forms.ValidationError('Password must contain at least one lowercase letter')
        weak = ['password', '12345678', 'qwerty', 'admin123', 'letmein']
        if password.lower() in weak:
            raise forms.ValidationError('This password is too common. Please choose a stronger one.')
        return password

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm  = cleaned_data.get('confirm_password')
        if password and confirm and password != confirm:
            raise forms.ValidationError('Passwords do not match.')
        return cleaned_data


# ══════════════════════════════════════════════════════════════════════
# AUTH VIEWS
# ══════════════════════════════════════════════════════════════════════

@never_cache
@csrf_protect
def home(request):
    if request.session.get('user_id'):
        return redirect('accounts:dashboard')
    return redirect('accounts:login')


@csrf_protect
@never_cache
def login(request):
    if request.session.get('user_id'):
        messages.info(request, 'You are already logged in')
        return redirect('accounts:dashboard')

    if request.method == 'POST':
        form = LoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            remember_me = form.cleaned_data.get('remember_me', False)

            try:
                user = User.objects.filter(email=username).first()
                if not user:
                    user = User.objects.filter(username=username).first()

                if user and check_password(password, user.password):
                    can_login, error_message = user.can_login()

                    if not can_login:
                        form.add_error(None, error_message)
                    else:
                        request.session['user_id']    = user.id
                        request.session['username']   = user.username
                        request.session['email']      = user.email
                        request.session['role']       = user.role
                        request.session['full_name']  = f"{user.first_name} {user.last_name}"
                        request.session['first_name'] = user.first_name

                        if remember_me:
                            request.session.set_expiry(1209600)  # 2 weeks
                        else:
                            request.session.set_expiry(0)        # Browser close

                        messages.success(request, f'Welcome back, {user.first_name}!')

                        # ── Role-based redirect (4 active roles + admin) ──
                        role_redirects = {
                            'ADMIN':      'travel_app:admin_dashboard',
                            'EMPLOYEE':   'travel_app:employee_dashboard',
                            'DEPT_SEC':   'travel_app:dept_secretary_dashboard',
                            'CAMPUS_SEC': 'travel_app:campus_secretary_dashboard',
                        }
                        return redirect(role_redirects.get(user.role, 'travel_app:employee_dashboard'))
                else:
                    form.add_error(None, 'Invalid email or password. Please try again.')

            except Exception:
                form.add_error(None, 'An error occurred during login. Please try again.')
    else:
        form = LoginForm()

    return render(request, 'accounts/login.html', {
        'form': form,
        'title': 'Login - BISU Travel Management'
    })


@csrf_protect
@never_cache
def register(request):
    if request.session.get('user_id'):
        messages.info(request, 'You are already logged in')
        return redirect('accounts:dashboard')

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    email        = form.cleaned_data['email']
                    first_name   = form.cleaned_data['first_name']
                    middle_name  = form.cleaned_data['middle_name']
                    last_name    = form.cleaned_data['last_name']
                    phone_number = form.cleaned_data['phone_number']
                    employee_id  = form.cleaned_data['employee_id']
                    password     = form.cleaned_data['password']
                    role         = form.cleaned_data['role']
                    campus       = form.cleaned_data['campus']
                    college      = form.cleaned_data['college']

                    # Generate unique username from email local part
                    base_username = email.split('@')[0].lower()
                    username = base_username
                    counter  = 1
                    while User.objects.filter(username=username).exists():
                        username = f"{base_username}{counter}"
                        counter += 1

                    User.objects.create(
                        username=username,
                        email=email,
                        employee_id=employee_id,
                        first_name=first_name,
                        middle_name=middle_name if middle_name else 'N/A',
                        last_name=last_name,
                        password=make_password(password),
                        role=role,
                        college=college,
                        campus=campus,
                        preference='NO_PREPAYMENT',
                        phone_number=phone_number,
                        on_travel=False,
                        is_approved=False,
                        is_active=True,
                    )

                    messages.warning(
                        request,
                        f'Account created! Your account is pending administrator approval. '
                        f'You will be notified at {email}.'
                    )
                    return redirect('accounts:login')

            except Exception as e:
                messages.error(request, f'An error occurred during registration: {str(e)}')
    else:
        form = RegisterForm()

    return render(request, 'accounts/register.html', {
        'form': form,
        'title': 'Register - BISU Travel Management'
    })


@never_cache
def logout(request):
    if request.session.get('user_id'):
        first_name = request.session.get('first_name', 'User')
        request.session.flush()
        messages.success(request, f'Goodbye, {first_name}! You have been logged out.')
    return redirect('accounts:login')


@never_cache
def dashboard(request):
    """Central redirect — sends each role to their correct dashboard."""
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to access the dashboard')
        return redirect('accounts:login')

    role_redirects = {
        'ADMIN':      'accounts:pending_approvals',
        'EMPLOYEE':   'travel_app:employee_dashboard',
        'DEPT_SEC':   'travel_app:dept_secretary_dashboard',
        'CAMPUS_SEC': 'travel_app:campus_secretary_dashboard',
    }
    return redirect(role_redirects.get(user.role, 'travel_app:employee_dashboard'))


# ══════════════════════════════════════════════════════════════════════
# ADMIN VIEWS
# ══════════════════════════════════════════════════════════════════════

@never_cache
def pending_approvals(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')
    if user.role != 'ADMIN':
        messages.error(request, 'Access denied.')
        return redirect('accounts:dashboard')

    pending_users = User.objects.filter(
        is_approved=False, is_active=True
    ).exclude(role='ADMIN').select_related('campus', 'college').order_by('-created_at')

    approved_users = User.objects.filter(
        is_approved=True, is_active=True
    ).exclude(role='ADMIN').select_related('campus', 'college', 'approved_by').order_by('-approved_at')[:10]

    context = {
        'title':         'Pending Approvals - Admin',
        'admin_user':    user,
        'pending_users': pending_users,
        'approved_users': approved_users,
        'total_pending': pending_users.count(),
        'total_approved': User.objects.filter(is_approved=True, is_active=True).exclude(role='ADMIN').count(),
        'total_users':   User.objects.filter(is_active=True).exclude(role='ADMIN').count(),
        'role_choices':  User.ROLE_CHOICES,
    }
    return render(request, 'accounts/pending_approvals.html', context)


# ══════════════════════════════════════════════════════════════════════
# UPDATED approve_user — Replace in accounts/views.py
# ══════════════════════════════════════════════════════════════════════

@csrf_protect
@never_cache
def approve_user(request, user_id):
    admin = get_authenticated_user(request)
    if not admin:
        return redirect('accounts:login')
    if admin.role != 'ADMIN':
        messages.error(request, 'Access denied.')
        return redirect('accounts:dashboard')

    user_to_approve = get_object_or_404(User, id=user_id)

    if user_to_approve.role == 'ADMIN':
        messages.error(request, 'Cannot approve admin accounts.')
        return redirect('accounts:pending_approvals')

    if user_to_approve.is_approved:
        messages.info(request, f'{user_to_approve.get_full_name()} is already approved.')
        return redirect('accounts:pending_approvals')

    if request.method == 'POST':
        assigned_role = request.POST.get('role', user_to_approve.role)
        valid_roles   = [r[0] for r in User.ROLE_CHOICES if r[0] != 'ADMIN']

        if assigned_role not in valid_roles:
            messages.error(request, 'Invalid role selected.')
            return redirect('accounts:pending_approvals')

        try:
            with transaction.atomic():
                user_to_approve.role        = assigned_role
                user_to_approve.is_approved = True
                user_to_approve.approved_by = admin
                user_to_approve.approved_at = timezone.now()
                user_to_approve.save()

                # Auto-link to travel if this was an invited user
                from travel_app.views import link_invited_user_to_travels
                link_invited_user_to_travels(user_to_approve)

                role_display = dict(User.ROLE_CHOICES).get(assigned_role, assigned_role)
                messages.success(
                    request,
                    f'✓ {user_to_approve.get_full_name()} approved as {role_display}.'
                )
        except Exception as e:
            messages.error(request, f'Error approving account: {str(e)}')

    return redirect('accounts:pending_approvals')

@csrf_protect
@never_cache
def reject_user(request, user_id):
    admin = get_authenticated_user(request)
    if not admin:
        return redirect('accounts:login')
    if admin.role != 'ADMIN':
        messages.error(request, 'Access denied.')
        return redirect('accounts:dashboard')

    user_to_reject = get_object_or_404(User, id=user_id)

    if user_to_reject.role == 'ADMIN':
        messages.error(request, 'Cannot reject admin accounts.')
        return redirect('accounts:pending_approvals')

    if request.method == 'POST':
        try:
            with transaction.atomic():
                name  = user_to_reject.get_full_name()
                email = user_to_reject.email
                user_to_reject.delete()
                messages.warning(request, f'Account rejected and deleted: {name} ({email}).')
        except Exception as e:
            messages.error(request, f'Error rejecting account: {str(e)}')

    return redirect('accounts:pending_approvals')


@never_cache
def view_user_details(request, user_id):
    admin = get_authenticated_user(request)
    if not admin:
        return redirect('accounts:login')
    if admin.role != 'ADMIN':
        messages.error(request, 'Access denied.')
        return redirect('accounts:dashboard')

    user = get_object_or_404(User, id=user_id)
    return render(request, 'accounts/user_details.html', {
        'title':        f'User Details - {user.get_full_name()}',
        'admin_user':   admin,
        'user':         user,
        'role_choices': User.ROLE_CHOICES,
    })


# ══════════════════════════════════════════════════════════════════════
# PROFILE VIEWS
# ══════════════════════════════════════════════════════════════════════

@never_cache
def profile(request):
    user = get_authenticated_user(request)
    if not user:
        messages.warning(request, 'Please login to view your profile')
        return redirect('accounts:login')

    # Travel stats using new TravelRecord model
    total_travels     = 0
    completed_travels = 0
    untagged_travels  = 0

    try:
        from travel_app.models import TravelRecord
        from django.utils import timezone as tz

        today = tz.now().date()

        my_travels = TravelRecord.objects.filter(participants__user=user)

        total_travels     = my_travels.count()
        completed_travels = my_travels.filter(end_date__lt=today).count()
        untagged_travels  = my_travels.filter(budget_source__isnull=True).count()

    except Exception:
        pass

    return render(request, 'accounts/profile.html', {
        'title':             f'Profile – {user.get_full_name()}',
        'user':              user,
        'total_travels':     total_travels,
        'completed_travels': completed_travels,
        'untagged_travels':  untagged_travels,
    })


@csrf_protect
@never_cache
def update_profile(request):
    user = get_authenticated_user(request)
    if not user:
        return redirect('accounts:login')

    if request.method != 'POST':
        return redirect('accounts:profile')

    form_type = request.POST.get('form_type')

    if form_type == 'personal':
        try:
            first_name   = request.POST.get('first_name', '').strip().title()
            middle_name  = request.POST.get('middle_name', '').strip().title() or 'N/A'
            last_name    = request.POST.get('last_name', '').strip().title()
            phone_number = ''.join(filter(str.isdigit, request.POST.get('phone_number', '')))
            employee_id  = request.POST.get('employee_id', '').strip().upper() or None
            preference   = request.POST.get('preference', user.preference)

            if not first_name or not last_name:
                messages.error(request, 'First and last name are required.')
                return redirect('accounts:profile')

            if not re.match(r"^[a-zA-Z\s\-']+$", first_name):
                messages.error(request, 'First name contains invalid characters.')
                return redirect('accounts:profile')

            if not re.match(r"^[a-zA-Z\s\-']+$", last_name):
                messages.error(request, 'Last name contains invalid characters.')
                return redirect('accounts:profile')

            if len(phone_number) != 11 or not phone_number.startswith('09'):
                messages.error(request, 'Phone number must be 11 digits starting with 09.')
                return redirect('accounts:profile')

            if User.objects.filter(phone_number=phone_number).exclude(id=user.id).exists():
                messages.error(request, 'This phone number is already used by another account.')
                return redirect('accounts:profile')

            if employee_id and User.objects.filter(employee_id=employee_id).exclude(id=user.id).exists():
                messages.error(request, 'This employee ID is already registered.')
                return redirect('accounts:profile')

            valid_prefs = [p[0] for p in User.PREFERENCE_CHOICES]
            if preference not in valid_prefs:
                preference = user.preference

            with transaction.atomic():
                user.first_name   = first_name
                user.middle_name  = middle_name
                user.last_name    = last_name
                user.phone_number = phone_number
                user.employee_id  = employee_id
                user.preference   = preference
                user.save()

            request.session['full_name']  = f"{user.first_name} {user.last_name}"
            request.session['first_name'] = user.first_name
            messages.success(request, 'Profile updated successfully!')

        except Exception as e:
            messages.error(request, f'Error updating profile: {str(e)}')

    elif form_type == 'password':
        current_password = request.POST.get('current_password', '')
        new_password     = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')

        if not check_password(current_password, user.password):
            messages.error(request, 'Current password is incorrect.')
            return redirect('accounts:profile')

        if len(new_password) < 8:
            messages.error(request, 'New password must be at least 8 characters.')
            return redirect('accounts:profile')

        if not re.search(r'[A-Z]', new_password):
            messages.error(request, 'Password must contain at least one uppercase letter.')
            return redirect('accounts:profile')

        if not re.search(r'[a-z]', new_password):
            messages.error(request, 'Password must contain at least one lowercase letter.')
            return redirect('accounts:profile')

        if new_password != confirm_password:
            messages.error(request, 'New passwords do not match.')
            return redirect('accounts:profile')

        if check_password(new_password, user.password):
            messages.error(request, 'New password must be different from your current password.')
            return redirect('accounts:profile')

        try:
            with transaction.atomic():
                user.password = make_password(new_password)
                user.save()
            messages.success(request, 'Password changed successfully!')
        except Exception as e:
            messages.error(request, f'Error changing password: {str(e)}')

    else:
        messages.error(request, 'Invalid form submission.')

    return redirect('accounts:profile')

# ══════════════════════════════════════════════════════════════════════
# INVITE REGISTRATION — Add to accounts/views.py
# ══════════════════════════════════════════════════════════════════════

@csrf_protect
@never_cache
def invite_register(request, token):
    """Registration page accessed via invite link."""
    from travel_app.models import TravelInvite

    # Validate token
    try:
        invite = TravelInvite.objects.select_related(
            'travel', 'invited_by'
        ).get(token=token)
    except TravelInvite.DoesNotExist:
        messages.error(request, 'Invalid invite link.')
        return redirect('accounts:login')

    if not invite.is_valid():
        if invite.is_used:
            messages.error(request, 'This invite link has already been used.')
        else:
            messages.error(request, 'This invite link has expired. Please ask the secretary for a new one.')
        return redirect('accounts:login')

    if request.session.get('user_id'):
        messages.info(request, 'You are already logged in.')
        return redirect('accounts:dashboard')

    # Parse pre-filled name from invite
    name_parts  = invite.invited_name.strip().split()
    first_name  = name_parts[0] if name_parts else ''
    last_name   = name_parts[-1] if len(name_parts) > 1 else ''
    middle_name = ' '.join(name_parts[1:-1]) if len(name_parts) > 2 else ''

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        # Override name fields with invite data (can't be changed)
        if form.is_valid():
            try:
                with transaction.atomic():
                    email        = form.cleaned_data['email']
                    phone_number = form.cleaned_data['phone_number']
                    employee_id  = form.cleaned_data['employee_id']
                    password     = form.cleaned_data['password']
                    role         = form.cleaned_data['role']
                    campus       = form.cleaned_data['campus']
                    college      = form.cleaned_data['college']

                    # Always use the name from the invite — not from form
                    base_username = email.split('@')[0].lower()
                    username = base_username
                    counter  = 1
                    while User.objects.filter(username=username).exists():
                        username = f"{base_username}{counter}"
                        counter += 1

                    new_user = User.objects.create(
                        username=username,
                        email=email,
                        employee_id=employee_id,
                        first_name=first_name.title(),
                        middle_name=middle_name.title() if middle_name else 'N/A',
                        last_name=last_name.title(),
                        password=make_password(password),
                        role=role,
                        college=college,
                        campus=campus,
                        preference='NO_PREPAYMENT',
                        phone_number=phone_number,
                        on_travel=False,
                        is_approved=False,
                        is_active=True,
                    )

                    # Mark invite as used and link to new user
                    invite.is_used    = True
                    invite.accepted_by = new_user
                    invite.save(update_fields=['is_used', 'accepted_by'])

                    messages.warning(
                        request,
                        f'Account created! Your account is pending administrator approval. '
                        f'Once approved, you will be automatically linked to the travel to '
                        f'{invite.travel.destination}.'
                    )
                    return redirect('accounts:login')

            except Exception as e:
                messages.error(request, f'An error occurred: {str(e)}')
    else:
        # Pre-fill form with name from invite
        form = RegisterForm(initial={
            'first_name':  first_name.title(),
            'middle_name': middle_name.title() if middle_name else '',
            'last_name':   last_name.title(),
        })

    return render(request, 'accounts/invite_register.html', {
        'form':        form,
        'invite':      invite,
        'first_name':  first_name.title(),
        'middle_name': middle_name.title() if middle_name else '',
        'last_name':   last_name.title(),
        'title':       'Accept Invite — BISU Travel Hub',
    })