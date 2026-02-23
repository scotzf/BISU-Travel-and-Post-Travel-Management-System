from django.shortcuts import redirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin
from .models import User


class AuthenticationMiddleware(MiddlewareMixin):
    """
    Middleware to handle authentication requirements for protected pages
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        super().__init__(get_response)
    
    def process_request(self, request):
        """Process each request before it reaches the view"""
        
        # Define public URLs that don't require authentication
        public_paths = [
            reverse('accounts:login'),
            reverse('accounts:register'),
            '/admin/',  # Django admin
        ]
        
        # Check if current path is public
        current_path = request.path
        is_public = any(current_path.startswith(path) for path in public_paths)
        
        # Check if user is authenticated
        user_id = request.session.get('user_id')
        
        # If not public and not authenticated, redirect to login
        if not is_public and not user_id:
            return redirect(f"{reverse('accounts:login')}?next={current_path}")
        
        # If authenticated and trying to access login/register, redirect to dashboard
        if user_id and current_path in [reverse('accounts:login'), reverse('accounts:register')]:
            return redirect('accounts:dashboard')
        
        return None


class UserContextMiddleware(MiddlewareMixin):
    """
    Middleware to add current user object to request
    Makes request.user available in all views and templates
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        super().__init__(get_response)
    
    def process_request(self, request):
        """Add user object to request if authenticated"""
        
        user_id = request.session.get('user_id')
        
        if user_id:
            try:
                # Fetch user from database
                user = User.objects.select_related('campus', 'college').get(id=user_id)
                request.user = user
                request.is_authenticated = True
            except User.DoesNotExist:
                # User no longer exists, clear session
                request.session.flush()
                request.user = None
                request.is_authenticated = False
        else:
            request.user = None
            request.is_authenticated = False
        
        return None


class SessionSecurityMiddleware(MiddlewareMixin):
    """
    Middleware to enhance session security
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        super().__init__(get_response)
    
    def process_request(self, request):
        """Validate session security"""
        
        if request.session.get('user_id'):
            # Store IP address for session validation (optional)
            session_ip = request.session.get('ip_address')
            current_ip = self.get_client_ip(request)
            
            if session_ip and session_ip != current_ip:
                # IP changed - potential session hijacking
                # In production, you might want to log this
                pass  # Keep session active for now
            else:
                # Store IP for future validation
                request.session['ip_address'] = current_ip
        
        return None
    
    def get_client_ip(self, request):
        """Get client IP address"""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip