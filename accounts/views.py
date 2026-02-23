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
from .models import User, College, Campus #TODO: Change the import to College instead of Department and rename all department to college


# ==================== FORMS ====================

class LoginForm(forms.Form):
    """Secure login form with validation"""
    username = forms.CharField(
        max_length=200,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your email',
            'autocomplete': 'username'
        }),
        error_messages={
            'required': 'Email address is required',
        }
    )
    
    password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Enter your password',
            'autocomplete': 'current-password'
        }),
        error_messages={
            'required': 'Password is required',
        }
    )
    
    remember_me = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={
            'class': 'form-check-input'
        })
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


class RegisterForm(forms.Form):
    """Comprehensive registration form with extensive validation"""
    
    # Personal Information
    first_name = forms.CharField(
        max_length=50,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'First Name'
        }),
        error_messages={
            'required': 'First name is required',
            'max_length': 'First name must not exceed 50 characters'
        }
    )
    
    middle_name = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Middle Name (Optional)'
        })
    )
    
    last_name = forms.CharField(
        max_length=50,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Last Name'
        }),
        error_messages={
            'required': 'Last name is required',
            'max_length': 'Last name must not exceed 50 characters'
        }
    )
    
    # Contact Information
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': 'example@bisu.edu.ph',
            'autocomplete': 'email'
        }),
        error_messages={
            'required': 'BISU email address is required',
            'invalid': 'Enter a valid email address'
        }
    )
    
    phone_number = forms.CharField(
        max_length=11,
        required=True,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '09XXXXXXXXX',
            'maxlength': '11'
        }),
        error_messages={
            'required': 'Phone number is required'
        }
    )
    
    employee_id = forms.CharField(
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Employee ID (Optional)'
        })
    )
    
    # Security
    password = forms.CharField(
        min_length=8,
        required=True,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Create a strong password',
            'autocomplete': 'new-password'
        }),
        error_messages={
            'required': 'Password is required',
            'min_length': 'Password must be at least 8 characters long'
        }
    )
    
    confirm_password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(attrs={
            'class': 'form-control',
            'placeholder': 'Confirm your password',
            'autocomplete': 'new-password'
        }),
        error_messages={
            'required': 'Password confirmation is required'
        }
    )
    
    # Role and Assignment - Uses REGISTRATION_ROLE_CHOICES (no ADMIN)
    role = forms.ChoiceField(
        choices=User.REGISTRATION_ROLE_CHOICES,
        required=True,
        widget=forms.Select(attrs={
            'class': 'form-control',
            'id': 'id_role'
        }),
        error_messages={
            'required': 'Please select a role'
        }
    )
    
    campus = forms.ModelChoiceField(
        queryset=Campus.objects.all().distinct(),
        required=True,
        widget=forms.Select(attrs={
            'class': 'form-control'
        }),
        empty_label='-- Select Campus --',
        error_messages={
            'required': 'Campus selection is required'
        }
    )
    
    college = forms.ModelChoiceField(
        queryset=College.objects.all().distinct(),
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-control',
            'id': 'id_college'
        }),
        empty_label='-- Select College (Optional) --'
    )
    
    # Preference - NOW OPTIONAL
    preference = forms.ChoiceField(
        choices=[('', '-- Select Payment Preference (Optional) --')] + list(User.PREFERENCE_CHOICES),
        required=False,
        widget=forms.Select(attrs={
            'class': 'form-control',
            'id': 'id_preference'
        })
    )

    def clean_first_name(self):
        """Validate and sanitize first name"""
        first_name = self.cleaned_data.get('first_name', '').strip()
        
        if not first_name:
            raise forms.ValidationError('First name cannot be empty')
        
        # Check for valid characters (letters, spaces, hyphens, apostrophes)
        if not re.match(r"^[a-zA-Z\s\-']+$", first_name):
            raise forms.ValidationError('First name contains invalid characters')
        
        return first_name.title()

    def clean_middle_name(self):
        """Validate and sanitize middle name"""
        middle_name = self.cleaned_data.get('middle_name', '').strip()
        
        if middle_name:
            if not re.match(r"^[a-zA-Z\s\-']+$", middle_name):
                raise forms.ValidationError('Middle name contains invalid characters')
            return middle_name.title()
        
        return None

    def clean_last_name(self):
        """Validate and sanitize last name"""
        last_name = self.cleaned_data.get('last_name', '').strip()
        
        if not last_name:
            raise forms.ValidationError('Last name cannot be empty')
        
        if not re.match(r"^[a-zA-Z\s\-']+$", last_name):
            raise forms.ValidationError('Last name contains invalid characters')
        
        return last_name.title()

    def clean_email(self):
        """Validate BISU email with strict rules"""
        email = self.cleaned_data.get('email', '').strip().lower()
        
        if not email:
            raise forms.ValidationError('Email address cannot be empty')
        
        # Validate email format
        try:
            validate_email(email)
        except ValidationError:
            raise forms.ValidationError('Enter a valid email address')
        
        # Check BISU domain
        if not email.endswith('@bisu.edu.ph'):
            raise forms.ValidationError(
                'Only official BISU email addresses (@bisu.edu.ph) are allowed'
            )
        
        # Check local part (before @)
        local_part = email.split('@')[0]
        if len(local_part) < 3:
            raise forms.ValidationError('Email address is too short')
        
        # Check for uniqueness
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError(
                'This email is already registered. Please use a different email or login.'
            )
        
        return email

    def clean_phone_number(self):
        """Validate Philippine mobile number format"""
        phone = self.cleaned_data.get('phone_number', '').strip()
        
        # Remove any non-digit characters
        phone = ''.join(filter(str.isdigit, phone))
        
        # Check length
        if len(phone) != 11:
            raise forms.ValidationError('Phone number must be exactly 11 digits')
        
        # Check Philippine mobile format (09XX-XXX-XXXX)
        if not phone.startswith('09'):
            raise forms.ValidationError('Phone number must start with 09')
        
        # Validate second digit (valid: 09[0-9])
        if phone[2] not in '0123456789':
            raise forms.ValidationError('Invalid phone number format')
        
        # Check for uniqueness
        if User.objects.filter(phone_number=phone).exists():
            raise forms.ValidationError(
                'This phone number is already registered'
            )
        
        return phone

    def clean_employee_id(self):
        """Validate employee ID if provided"""
        employee_id = self.cleaned_data.get('employee_id', '').strip()
        
        if employee_id:
            # Remove spaces and convert to uppercase
            employee_id = employee_id.replace(' ', '').upper()
            
            # Check format (alphanumeric only)
            if not re.match(r'^[A-Z0-9\-]+$', employee_id):
                raise forms.ValidationError(
                    'Employee ID can only contain letters, numbers, and hyphens'
                )
            
            # Check length
            if len(employee_id) < 3 or len(employee_id) > 20:
                raise forms.ValidationError(
                    'Employee ID must be between 3 and 20 characters'
                )
            
            # Check uniqueness
            if User.objects.filter(employee_id=employee_id).exists():
                raise forms.ValidationError(
                    'This employee ID is already registered'
                )
            
            return employee_id
        
        return None

    def clean_password(self):
        """Validate password strength - SIMPLIFIED"""
        password = self.cleaned_data.get('password', '')
        
        if len(password) < 8:
            raise forms.ValidationError('Password must be at least 8 characters long')
        
        # Check for at least one uppercase letter
        if not re.search(r'[A-Z]', password):
            raise forms.ValidationError('Password must contain at least one uppercase letter')
        
        # Check for at least one lowercase letter
        if not re.search(r'[a-z]', password):
            raise forms.ValidationError('Password must contain at least one lowercase letter')
        
        # Check for common weak passwords
        weak_passwords = ['password', '12345678', 'qwerty', 'admin123', 'letmein']
        if password.lower() in weak_passwords:
            raise forms.ValidationError('This password is too common. Please choose a stronger password')
        
        return password

    def clean(self):
        """Additional validation for password matching and role-based college"""
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')
        role = cleaned_data.get('role')
        college = cleaned_data.get('college')
        
        # Password matching validation
        if password and confirm_password:
            if password != confirm_password:
                raise forms.ValidationError('Passwords do not match. Please try again.')
        
        # College validation based on role
        # President and Director should NOT have a college
        if role in ['PRESIDENT', 'DIRECTOR','CASHIER','BUDGET']:
            if college:
                raise forms.ValidationError(
                    f'{dict(User.ROLE_CHOICES).get(role)} role should not be assigned to a specific college.'
                )
            # Force college to None
            cleaned_data['college'] = None
        
        return cleaned_data


# ==================== VIEWS ====================
@never_cache
@csrf_protect
def home(request):
    """Redirect to login page"""
    if request.session.get('user_id'):
        return redirect('accounts:dashboard')
    return redirect('accounts:login')


@csrf_protect
@never_cache
def login(request):
    """Handle user authentication with security measures"""
    
    # Redirect authenticated users
    if request.session.get('user_id'):
        messages.info(request, 'You are already logged in')
        return redirect('accounts:dashboard')

    if 'show_pending_modal' in request.session:
        del request.session['show_pending_modal']
    
    if request.method == 'POST':
        form = LoginForm(request.POST)
        
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            remember_me = form.cleaned_data.get('remember_me', False)
            
            try:
                # Try to find user by email
                user = User.objects.filter(email=username).first()
                
                # If not found by email, try username
                if not user:
                    user = User.objects.filter(username=username).first()
                
                # Verify user exists and password is correct
                if user and check_password(password, user.password):
                    
                    # Check if user can login (approval check)
                    can_login, error_message = user.can_login()
                    
                    if not can_login:
                        form.add_error(None, error_message)
                    else:
                        # Set session data
                        request.session['user_id'] = user.id
                        request.session['username'] = user.username
                        request.session['email'] = user.email
                        request.session['role'] = user.role
                        request.session['full_name'] = f"{user.first_name} {user.last_name}"
                        request.session['first_name'] = user.first_name
                        
                        # Set session expiry based on remember me
                        if remember_me:
                            request.session.set_expiry(1209600)  # 2 weeks
                        else:
                            request.session.set_expiry(0)  # Browser close
                        
                        # Success message
                        messages.success(
                            request,
                            f'Welcome back, {user.first_name}! You have successfully logged in.'
                        )
                        
                        # ==================== ROLE-BASED REDIRECT ====================
                        if user.role == 'ADMIN':
                            return redirect('accounts:pending_approvals')
                        
                        elif user.role == 'EMPLOYEE':
                            return redirect('travel_app:employee_dashboard')
                        
                        elif user.role == 'DEPT_SEC':
                            return redirect('travel_app:secretary_dashboard')
                        
                        elif user.role == 'CAMPUS_SEC':
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
                        
                        else:
                            # Fallback for any unexpected roles
                            return redirect('travel_app:employee_dashboard')
                        # ===========================================================
                else:
                    # Invalid credentials
                    form.add_error(None, 'Invalid email or password. Please try again.')
                    
            except Exception as e:
                # Log the error in production
                form.add_error(None, 'An error occurred during login. Please try again.')
    else:
        form = LoginForm()
    
    context = {
        'form': form,
        'title': 'Login - BISU Travel Management'
    }
    
    return render(request, 'accounts/login.html', context)


@csrf_protect
@never_cache
def register(request):
    """Handle user registration with comprehensive validation"""
    
    # Redirect authenticated users
    if request.session.get('user_id'):
        messages.info(request, 'You are already logged in')
        return redirect('accounts:dashboard')
    
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        
        if form.is_valid():
            try:
                with transaction.atomic():
                    # Extract cleaned data
                    email = form.cleaned_data['email']
                    first_name = form.cleaned_data['first_name']
                    middle_name = form.cleaned_data['middle_name']
                    last_name = form.cleaned_data['last_name']
                    phone_number = form.cleaned_data['phone_number']
                    employee_id = form.cleaned_data['employee_id']
                    password = form.cleaned_data['password']
                    role = form.cleaned_data['role']
                    campus = form.cleaned_data['campus']
                    college = form.cleaned_data['college']
                    preference = form.cleaned_data.get('preference', '')
                    
                    # Generate unique username from email
                    base_username = email.split('@')[0].lower()
                    username = base_username
                    counter = 1
                    
                    while User.objects.filter(username=username).exists():
                        username = f"{base_username}{counter}"
                        counter += 1
                    
                    # Set default preference if not provided
                    if not preference:
                        preference = 'NO_PREPAYMENT'
                    
                    # Create new user - ALL accounts need approval
                    user = User.objects.create(
                        username=username,
                        email=email,
                        employee_id=employee_id if employee_id else None,
                        first_name=first_name,
                        middle_name=middle_name if middle_name else 'N/A',
                        last_name=last_name,
                        password=make_password(password),
                        role=role,
                        college=college,
                        campus=campus,
                        preference=preference,
                        phone_number=phone_number,
                        on_travel=False,
                        is_approved=False,
                        is_active=True
                    )
                    
                    # Success message
                    messages.warning(
                        request,
                        f'Account created successfully! Your account is pending administrator approval. '
                        f'Please wait for confirmation before logging in. You will receive notification at {email}.'
                    )
                    request.session['show_pending_modal'] = True
                    if request.session.get('show_pending_modal'):
                        del request.session['show_pending_modal']

                    return redirect('accounts:login')
                    
            except Exception as e:
                messages.error(
                    request,
                    f'An error occurred during registration: {str(e)}'
                )
    else:
        form = RegisterForm()
    
    context = {
        'form': form,
        'title': 'Register - BISU Travel Management'
    }
    
    return render(request, 'accounts/register.html', context)


@never_cache
def logout(request):
    """Handle user logout and session cleanup"""
    
    if request.session.get('user_id'):
        first_name = request.session.get('first_name', 'User')
        
        # Clear session
        request.session.flush()
        
        # Success message
        messages.success(
            request,
            f'Goodbye, {first_name}! You have been successfully logged out.'
        )
    
    return redirect('accounts:login')

@never_cache
def dashboard(request):
    """Dashboard view for regular users"""
    
    if not request.session.get('user_id'):
        messages.warning(request, 'Please login to access the dashboard')
        return redirect('accounts:login')
    
    # Get current user
    try:
        user = User.objects.get(id=request.session['user_id'])
    except User.DoesNotExist:
        request.session.flush()
        messages.error(request, 'User not found. Please login again.')
        return redirect('accounts:login')
    
    # Redirect admin to approval page
    if user.role == 'ADMIN':
        return redirect('accounts:pending_approvals')
    
    context = {
        'title': 'Dashboard - BISU Travel Management',
        'user': user
    }
    
    return render(request, 'accounts/dashboard.html', context)


# ==================== ADMIN VIEWS ====================

@never_cache
def pending_approvals(request):
    """Admin dashboard to view and manage pending account approvals"""
    
    # Check if user is logged in
    if not request.session.get('user_id'):
        messages.warning(request, 'Please login to access this page')
        return redirect('accounts:login')
    
    # Get current user
    try:
        admin_user = User.objects.get(id=request.session['user_id'])
    except User.DoesNotExist:
        request.session.flush()
        messages.error(request, 'User not found. Please login again.')
        return redirect('accounts:login')
    
    # Check if user is admin
    if admin_user.role != 'ADMIN':
        messages.error(request, 'Access denied. Admin privileges required.')
        return redirect('accounts:dashboard')
    
    # Get all pending approvals (ordered by newest first)
    pending_users = User.objects.filter(
        is_approved=False,
        is_active=True
    ).exclude(
        role='ADMIN'
    ).select_related('campus', 'college').order_by('-created_at')
    
    # Get recently approved users (last 10)
    approved_users = User.objects.filter(
        is_approved=True,
        is_active=True
    ).exclude(
        role='ADMIN'
    ).select_related('campus', 'college', 'approved_by').order_by('-approved_at')[:10]
    
    # Get statistics
    total_pending = pending_users.count()
    total_approved = User.objects.filter(is_approved=True, is_active=True).exclude(role='ADMIN').count()
    total_users = User.objects.filter(is_active=True).exclude(role='ADMIN').count()
    
    context = {
        'title': 'Pending Approvals - Admin Dashboard',
        'admin_user': admin_user,
        'pending_users': pending_users,
        'approved_users': approved_users,
        'total_pending': total_pending,
        'total_approved': total_approved,
        'total_users': total_users,
        'role_choices': User.ROLE_CHOICES,
    }
    
    return render(request, 'accounts/pending_approvals.html', context)


@csrf_protect
@never_cache
def approve_user(request, user_id):
    """Approve a user account with the requested role or a different role"""
    
    # Check if user is logged in
    if not request.session.get('user_id'):
        messages.warning(request, 'Please login to access this page')
        return redirect('accounts:login')
    
    # Get admin user
    try:
        admin_user = User.objects.get(id=request.session['user_id'])
    except User.DoesNotExist:
        request.session.flush()
        messages.error(request, 'User not found. Please login again.')
        return redirect('accounts:login')
    
    # Check if user is admin
    if admin_user.role != 'ADMIN':
        messages.error(request, 'Access denied. Admin privileges required.')
        return redirect('accounts:dashboard')
    
    # Get user to approve
    user_to_approve = get_object_or_404(User, id=user_id)
    
    # Prevent approving admin accounts
    if user_to_approve.role == 'ADMIN':
        messages.error(request, 'Cannot approve admin accounts through this interface.')
        return redirect('accounts:pending_approvals')
    
    # Check if already approved
    if user_to_approve.is_approved:
        messages.info(request, f'{user_to_approve.first_name} {user_to_approve.last_name} is already approved.')
        return redirect('accounts:pending_approvals')
    
    if request.method == 'POST':
        # Get the role to assign (could be same as requested or different)
        assigned_role = request.POST.get('role', user_to_approve.role)
        
        # Validate role
        valid_roles = [role[0] for role in User.ROLE_CHOICES if role[0] != 'ADMIN']
        if assigned_role not in valid_roles:
            messages.error(request, 'Invalid role selected.')
            return redirect('accounts:pending_approvals')
        
        try:
            with transaction.atomic():
                # Update user
                user_to_approve.role = assigned_role
                user_to_approve.is_approved = True
                user_to_approve.approved_by = admin_user
                user_to_approve.approved_at = timezone.now()
                user_to_approve.save()
                
                # Success message
                role_display = dict(User.ROLE_CHOICES).get(assigned_role, assigned_role)
                messages.success(
                    request,
                    f'✓ Account approved! {user_to_approve.first_name} {user_to_approve.last_name} '
                    f'has been approved as {role_display}.'
                )
                
        except Exception as e:
            messages.error(request, f'Error approving account: {str(e)}')
    
    return redirect('accounts:pending_approvals')


@csrf_protect
@never_cache
def reject_user(request, user_id):
    """Reject a user account (deactivate it)"""
    
    # Check if user is logged in
    if not request.session.get('user_id'):
        messages.warning(request, 'Please login to access this page')
        return redirect('accounts:login')
    
    # Get admin user
    try:
        admin_user = User.objects.get(id=request.session['user_id'])
    except User.DoesNotExist:
        request.session.flush()
        messages.error(request, 'User not found. Please login again.')
        return redirect('accounts:login')
    
    # Check if user is admin
    if admin_user.role != 'ADMIN':
        messages.error(request, 'Access denied. Admin privileges required.')
        return redirect('accounts:dashboard')
    
    # Get user to reject
    user_to_reject = get_object_or_404(User, id=user_id)
    
    # Prevent rejecting admin accounts
    if user_to_reject.role == 'ADMIN':
        messages.error(request, 'Cannot reject admin accounts.')
        return redirect('accounts:pending_approvals')
    
    if request.method == 'POST':
        try:
            with transaction.atomic():
                # Delete the account
                user_to_reject.delete()
                
                # Success message
                messages.warning(
                    request,
                    f'Account rejected and deleted: {user_to_reject.first_name} {user_to_reject.last_name} '
                    f'({user_to_reject.email}).'
                )
                
        except Exception as e:
            messages.error(request, f'Error rejecting account: {str(e)}')
    
    return redirect('accounts:pending_approvals')


@csrf_protect
@never_cache
def view_user_details(request, user_id):
    """View detailed information about a pending user account"""
    
    # Check if user is logged in
    if not request.session.get('user_id'):
        messages.warning(request, 'Please login to access this page')
        return redirect('accounts:login')
    
    # Get admin user
    try:
        admin_user = User.objects.get(id=request.session['user_id'])
    except User.DoesNotExist:
        request.session.flush()
        messages.error(request, 'User not found. Please login again.')
        return redirect('accounts:login')
    
    # Check if user is admin
    if admin_user.role != 'ADMIN':
        messages.error(request, 'Access denied. Admin privileges required.')
        return redirect('accounts:dashboard')
    
    # Get user details
    user = get_object_or_404(User, id=user_id)
    
    context = {
        'title': f'User Details - {user.first_name} {user.last_name}',
        'admin_user': admin_user,
        'user': user,
        'role_choices': User.ROLE_CHOICES,
    }
    
    return render(request, 'accounts/user_details.html', context)\
    
# ==================== PROFILE VIEWS ====================
# Add these two functions to accounts/views.py

@never_cache
def profile(request):
    """View the logged-in user's profile"""

    if not request.session.get('user_id'):
        messages.warning(request, 'Please login to view your profile')
        return redirect('accounts:login')

    try:
        user = User.objects.select_related('campus', 'college').get(
            id=request.session['user_id']
        )
    except User.DoesNotExist:
        request.session.flush()
        messages.error(request, 'User not found. Please login again.')
        return redirect('accounts:login')

    # ── Travel stats (only meaningful for employees / participants) ──
    total_travels    = 0
    completed_travels = 0
    pending_travels  = 0

    try:
        # Import here to avoid circular imports at module level
        from travel_app.models import OfficialTravel
        from django.utils import timezone as tz

        today = tz.now().date()

        my_travels = OfficialTravel.objects.filter(
            participants_group__users=user
        ).select_related('travel_order')

        total_travels     = my_travels.count()
        completed_travels = my_travels.filter(
            end_date__lt=today,
            travel_order__status='APPROVED'
        ).count()
        pending_travels   = my_travels.filter(
            travel_order__status__in=['PENDING_DEAN', 'PENDING_DIRECTOR']
        ).count()

    except Exception:
        # travel_app may not be installed or user has no travels — safe to ignore
        pass

    context = {
        'title': f'Profile – {user.get_full_name()}',
        'user': user,
        'total_travels':     total_travels,
        'completed_travels': completed_travels,
        'pending_travels':   pending_travels,
    }

    return render(request, 'accounts/profile.html', context)


@csrf_protect
@never_cache
def update_profile(request):
    """Handle profile update form submissions (personal info or password)"""

    if not request.session.get('user_id'):
        messages.warning(request, 'Please login to update your profile')
        return redirect('accounts:login')

    try:
        user = User.objects.get(id=request.session['user_id'])
    except User.DoesNotExist:
        request.session.flush()
        return redirect('accounts:login')

    if request.method != 'POST':
        return redirect('accounts:profile')

    form_type = request.POST.get('form_type')

    # ── Personal Info Update ──
    if form_type == 'personal':
        try:
            first_name   = request.POST.get('first_name', '').strip().title()
            middle_name  = request.POST.get('middle_name', '').strip().title() or 'N/A'
            last_name    = request.POST.get('last_name', '').strip().title()
            phone_number = ''.join(filter(str.isdigit, request.POST.get('phone_number', '')))
            employee_id  = request.POST.get('employee_id', '').strip().upper() or None
            preference   = request.POST.get('preference', user.preference)

            # Basic validation
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

            # Check phone uniqueness (exclude self)
            if User.objects.filter(phone_number=phone_number).exclude(id=user.id).exists():
                messages.error(request, 'This phone number is already used by another account.')
                return redirect('accounts:profile')

            # Check employee ID uniqueness (exclude self)
            if employee_id and User.objects.filter(employee_id=employee_id).exclude(id=user.id).exists():
                messages.error(request, 'This employee ID is already registered.')
                return redirect('accounts:profile')

            # Check preference is valid
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

            # Update session name
            request.session['full_name']  = f"{user.first_name} {user.last_name}"
            request.session['first_name'] = user.first_name

            messages.success(request, 'Profile updated successfully!')

        except Exception as e:
            messages.error(request, f'Error updating profile: {str(e)}')

    # ── Password Change ──
    elif form_type == 'password':
        current_password = request.POST.get('current_password', '')
        new_password     = request.POST.get('new_password', '')
        confirm_password = request.POST.get('confirm_password', '')

        # Verify current password
        if not check_password(current_password, user.password):
            messages.error(request, 'Current password is incorrect.')
            return redirect('accounts:profile')

        # Length check
        if len(new_password) < 8:
            messages.error(request, 'New password must be at least 8 characters.')
            return redirect('accounts:profile')

        # Uppercase check
        if not re.search(r'[A-Z]', new_password):
            messages.error(request, 'Password must contain at least one uppercase letter.')
            return redirect('accounts:profile')

        # Lowercase check
        if not re.search(r'[a-z]', new_password):
            messages.error(request, 'Password must contain at least one lowercase letter.')
            return redirect('accounts:profile')

        # Match check
        if new_password != confirm_password:
            messages.error(request, 'New passwords do not match.')
            return redirect('accounts:profile')

        # Same as current check
        if check_password(new_password, user.password):
            messages.error(request, 'New password must be different from your current password.')
            return redirect('accounts:profile')

        try:
            with transaction.atomic():
                user.password = make_password(new_password)
                user.save()

            messages.success(request, 'Password changed successfully! Please keep it safe.')

        except Exception as e:
            messages.error(request, f'Error changing password: {str(e)}')

    else:
        messages.error(request, 'Invalid form submission.')

    return redirect('accounts:profile')