import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "travel_mgmt.settings")
django.setup()

from accounts.models import User, Campus, College
from django.contrib.auth.hashers import make_password

# Ensure the campus exists
campus_obj, _ = Campus.objects.get_or_create(
    name="BISU Candijay",
    defaults={
        "municipality": "Candijay",
        "province": "Bohol",
        "street": "",
        "barangay": ""
    }
)

# Create departments for BISU Candijay
department_names = [
    "School of Advanced Studies",
    "College of Fisheries and Marine Sciences",
    "College of Teacher Education",
    "College of Business and Management",
    "College of Sciences",
]

for name in department_names:
    dept_obj, created = College.objects.get_or_create(name=name)
    if created:
        print(f"Created department: {dept_obj.name}")
    else:
        print(f"Department already exists: {dept_obj.name}")

# Pick a department for the admin (optional, you can pick any)
admin_department = College.objects.get(name="School of Advanced Studies")

# Create admin user
admin_user, created = User.objects.get_or_create(
    username="admin",
    defaults={
        "email": "admin@bisu.edu.ph",
        "password": make_password("Admin123"),
        "first_name": "System",
        "last_name": "Administrator",
        "role": "ADMIN",
        "phone_number": "09123456789",
        "campus": campus_obj,
        "college": admin_department,
        "preference": "PREPAYMENT",
        "is_approved": True,
        "is_active": True
    }
)

if created:
    print(f"Admin user '{admin_user.username}' created successfully.")
else:
    print(f"Admin user '{admin_user.username}' already exists.")
