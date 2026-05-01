import os
import sys
import django
import random
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'travel_mgmt.settings')
django.setup()

from accounts.models import User, College, Campus
from travel_app.models import TravelRecord, TravelParticipant, BudgetSource

# ── Pull existing data to seed realistically ────────────────────────
users    = list(User.objects.filter(is_active=True, is_approved=True))
colleges = list(College.objects.all())
campuses = list(Campus.objects.all())
sources  = list(BudgetSource.objects.filter(is_active=True))

if not users:
    print("No approved users found. Create some users first.")
    sys.exit(1)

destinations = [
    "Cebu City", "Davao City", "Manila", "Tagbilaran City", "Bohol",
    "Cagayan de Oro", "Iloilo City", "Bacolod", "Dumaguete", "Tacloban",
    "Butuan", "Zamboanga", "General Santos", "Legazpi", "Naga City",
    "Ubay, Bohol", "Talibon, Bohol", "Jagna, Bohol", "Loay, Bohol",
    "Panglao, Bohol", "Tubigon, Bohol", "Calape, Bohol", "Candijay, Bohol",
]

purposes = [
    "Attendance at Regional CHED Conference",
    "Training and Capacity Building Seminar",
    "Inter-Campus Academic Coordination Meeting",
    "Research Dissemination Forum",
    "Curriculum Development Workshop",
    "Regional Sports Competition",
    "Faculty Development Program",
    "Budget Hearing at DBM Regional Office",
    "Accreditation Visit by AACCUP",
    "Job Fair and Industry Linkage Activity",
    "Community Extension Service Activity",
    "National Higher Education Summit",
    "BISU Board of Regents Meeting",
    "Procurement Training Workshop",
    "Gender and Development Seminar",
]

import datetime

print("Seeding 1000 TravelRecords...")

for i in range(1000):
    creator      = random.choice(users)
    destination  = random.choice(destinations)
    purpose      = random.choice(purposes)
    days_ago     = random.randint(1, 730)
    start_date   = datetime.date.today() - datetime.timedelta(days=days_ago)
    duration     = random.randint(1, 7)
    end_date     = start_date + datetime.timedelta(days=duration)
    out_of_prov  = destination not in ["Ubay, Bohol", "Talibon, Bohol", "Jagna, Bohol",
                                        "Loay, Bohol", "Panglao, Bohol", "Tubigon, Bohol",
                                        "Calape, Bohol", "Candijay, Bohol", "Tagbilaran City", "Bohol"]
    scope        = random.choice(['COLLEGE', 'CAMPUS'])
    amount       = Decimal(str(round(random.uniform(1000, 50000), 2)))

    travel = TravelRecord.objects.create(
        destination=destination,
        start_date=start_date,
        end_date=end_date,
        purpose=purpose,
        is_out_of_province=out_of_prov,
        scope=scope,
        created_by=creator,
        notes="Auto-generated seed data.",
    )

    # Add creator as participant
    TravelParticipant.objects.create(
        travel_record=travel,
        user=creator,
        is_registered=True,
    )

    # Add 1-4 more random participants
    extra_count = random.randint(1, 4)
    others      = random.sample([u for u in users if u != creator], min(extra_count, len(users) - 1))
    for u in others:
        TravelParticipant.objects.get_or_create(
            travel_record=travel,
            user=u,
        )

    # Tag budget on ~60% of records
    if sources and random.random() < 0.6:
        source = random.choice(sources)
        travel.budget_source    = source
        travel.amount_deducted  = amount
        travel.budget_tagged_by = creator
        travel.budget_tagged_at = datetime.datetime.now()
        travel.save(update_fields=[
            'budget_source', 'amount_deducted',
            'budget_tagged_by', 'budget_tagged_at'
        ])

    if (i + 1) % 100 == 0:
        print(f"  {i + 1}/1000 created...")

print("Done. 1000 travel records seeded.")