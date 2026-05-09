import os
import django
import random
from datetime import date, timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "travel_mgmt.settings")
django.setup()

from accounts.models import User, Campus, College
from travel_app.models import BudgetSource, TravelRecord, TravelParticipant
from django.contrib.auth.hashers import make_password

PASSWORD = make_password("Password123")

campus = Campus.objects.first()
colleges = list(College.objects.all())

print(f"Campus: {campus}")
print(f"Colleges: {[c.name for c in colleges]}")

# ══════════════════════════════════════════════════════════════════════
# 1. CAMPUS SECRETARY
# ══════════════════════════════════════════════════════════════════════
campus_sec, created = User.objects.get_or_create(
    username="campus_sect",
    defaults={
        "email": "campus.sect@bisu.edu.ph",
        "password": PASSWORD,
        "first_name": "Campus",
        "last_name": "Secretary",
        "role": "CAMPUS_SEC",
        "campus": campus,
        "college": None,
        "preference": "NO_PREPAYMENT",
        "phone_number": "09100000001",
        "is_approved": True,
        "is_active": True,
    }
)
print(f"{'Created' if created else 'Exists'}: Campus Secretary ({campus_sec.username})")

# ══════════════════════════════════════════════════════════════════════
# 2. DEPT SECRETARY PER COLLEGE
# ══════════════════════════════════════════════════════════════════════
dept_secs = {}
for i, college in enumerate(colleges):
    code = college.code.lower() if college.code else f"col{i}"
    username = f"sect_{code}"
    phone = f"0910000{str(i+10).zfill(4)}"
    sec, created = User.objects.get_or_create(
        username=username,
        defaults={
            "email": f"sect.{code}@bisu.edu.ph",
            "password": PASSWORD,
            "first_name": f"{college.code}",
            "last_name": "Secretary",
            "role": "DEPT_SEC",
            "campus": campus,
            "college": college,
            "preference": "NO_PREPAYMENT",
            "phone_number": phone,
            "is_approved": True,
            "is_active": True,
        }
    )
    dept_secs[college.id] = sec
    print(f"{'Created' if created else 'Exists'}: Dept Secretary for {college.name} ({sec.username})")

# ══════════════════════════════════════════════════════════════════════
# 3. BUDGET SOURCES PER COLLEGE + CAMPUS
# ══════════════════════════════════════════════════════════════════════
FISCAL_YEAR = 2026
budget_names_college = ["Travel Expense", "Research Fund", "Training Budget"]
budget_names_campus  = ["Campus Travel Pool", "Campus Research Fund"]

for college in colleges:
    for i, bname in enumerate(budget_names_college):
        src, created = BudgetSource.objects.get_or_create(
            budget_name=bname,
            fiscal_year=FISCAL_YEAR,
            budget_scope="COLLEGE",
            college=college,
            defaults={
                "budget_amount": random.choice([50000, 75000, 100000]),
                "description": f"{bname} for {college.name}",
                "is_active": True,
            }
        )
        print(f"{'Created' if created else 'Exists'}: Budget source '{bname}' for {college.name}")

for bname in budget_names_campus:
    src, created = BudgetSource.objects.get_or_create(
        budget_name=bname,
        fiscal_year=FISCAL_YEAR,
        budget_scope="CAMPUS",
        college=None,
        defaults={
            "budget_amount": 200000,
            "description": f"{bname} for {campus.name}",
            "is_active": True,
        }
    )
    print(f"{'Created' if created else 'Exists'}: Campus budget source '{bname}'")

# ══════════════════════════════════════════════════════════════════════
# 4. 10 EMPLOYEES PER COLLEGE
# ══════════════════════════════════════════════════════════════════════
FIRST_NAMES = ["Juan", "Maria", "Jose", "Ana", "Pedro", "Rosa", "Carlos", "Liza", "Mark", "Grace",
               "Ryan", "Faith", "Aaron", "Hope", "Elijah", "Joy", "Nathan", "Claire", "Daniel", "Angel"]
LAST_NAMES  = ["Santos", "Reyes", "Cruz", "Bautista", "Ocampo", "Garcia", "Torres", "Flores",
               "Ramos", "Gomez", "Dela Cruz", "Mendoza", "Aquino", "Villanueva", "Castillo"]

college_employees = {}
phone_counter = 500

for college in colleges:
    employees = []
    code = college.code.lower() if college.code else "col"
    for j in range(10):
        fname = FIRST_NAMES[(j + colleges.index(college) * 10) % len(FIRST_NAMES)]
        lname = LAST_NAMES[(j + colleges.index(college) * 3) % len(LAST_NAMES)]
        username = f"{code}_emp{j+1}"
        phone = f"09{str(phone_counter).zfill(9)}"
        phone_counter += 1
        emp, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": f"{username}@bisu.edu.ph",
                "password": PASSWORD,
                "first_name": fname,
                "last_name": lname,
                "role": "EMPLOYEE",
                "campus": campus,
                "college": college,
                "preference": "NO_PREPAYMENT",
                "phone_number": phone,
                "is_approved": True,
                "is_active": True,
            }
        )
        employees.append(emp)
        print(f"{'Created' if created else 'Exists'}: Employee {emp.get_full_name()} ({college.name})")
    college_employees[college.id] = employees

# ══════════════════════════════════════════════════════════════════════
# 5. TRAVEL RECORDS — mix of college and campus scope
# ══════════════════════════════════════════════════════════════════════
DESTINATIONS = [
    "Cebu City, Cebu", "Tagbilaran City, Bohol", "Davao City, Davao del Sur",
    "Manila, Metro Manila", "Dumaguete City, Negros Oriental", "Iloilo City, Iloilo",
    "Cagayan de Oro, Misamis Oriental", "Zamboanga City, Zamboanga del Sur",
    "Butuan City, Agusan del Norte", "General Santos City, South Cotabato",
]
PURPOSES = [
    "Regional seminar on educational leadership and management",
    "National conference on research and innovation",
    "Training on financial management and budget utilization",
    "Workshop on curriculum development and instructional design",
    "Inter-campus coordination meeting on academic programs",
    "Coastal resource management training for faculty",
    "Professional development seminar for STEM educators",
    "Regional sports competition as team escorts",
]

travel_records = []
start = date(2026, 1, 1)

# College-scoped travels (2 per college)
for college in colleges:
    employees = college_employees[college.id]
    sec = dept_secs[college.id]
    for t in range(2):
        travel_date = start + timedelta(days=random.randint(0, 120))
        dest = random.choice(DESTINATIONS)
        is_out = dest not in ["Tagbilaran City, Bohol"]
        travel = TravelRecord.objects.create(
            destination=dest,
            start_date=travel_date,
            end_date=travel_date + timedelta(days=random.randint(1, 3)),
            purpose=random.choice(PURPOSES),
            is_out_of_province=is_out,
            scope="COLLEGE",
            created_by=sec,
            notes="",
        )
        # Add 3-5 participants from same college
        participants = random.sample(employees, min(random.randint(3, 5), len(employees)))
        for emp in participants:
            TravelParticipant.objects.get_or_create(
                travel_record=travel,
                user=emp,
                defaults={
                    "is_registered": True,
                    "college_name": college.name,
                    "campus_name": campus.name,
                }
            )
        travel.refresh_scope()
        travel_records.append(travel)
        print(f"Created COLLEGE travel: {dest} ({college.name})")

# Campus-scoped travels (3 total, cross-college participants)
for t in range(3):
    travel_date = start + timedelta(days=random.randint(0, 120))
    dest = random.choice(DESTINATIONS)
    is_out = dest not in ["Tagbilaran City, Bohol"]
    travel = TravelRecord.objects.create(
        destination=dest,
        start_date=travel_date,
        end_date=travel_date + timedelta(days=random.randint(1, 4)),
        purpose=random.choice(PURPOSES),
        is_out_of_province=is_out,
        scope="CAMPUS",
        created_by=campus_sec,
        notes="",
    )
    # Add 1-2 participants from each college
    for college in colleges:
        emps = random.sample(college_employees[college.id], min(2, len(college_employees[college.id])))
        for emp in emps:
            TravelParticipant.objects.get_or_create(
                travel_record=travel,
                user=emp,
                defaults={
                    "is_registered": True,
                    "college_name": college.name,
                    "campus_name": campus.name,
                }
            )
    travel.refresh_scope()
    travel_records.append(travel)
    print(f"Created CAMPUS travel: {dest}")

print(f"\n✅ Done!")
print(f"   Campus Secretary: campus_sect / Password123")
print(f"   Dept Secretaries: sect_<college_code> / Password123")
print(f"   Employees: <college_code>_emp1 to <college_code>_emp10 / Password123")
print(f"   Travel records created: {len(travel_records)}")
print(f"   Budget sources: {BudgetSource.objects.count()} total")