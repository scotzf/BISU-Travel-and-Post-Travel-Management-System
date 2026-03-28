# travel_app/management/commands/seed_data.py
#
# Usage:
#   python manage.py seed_data          # generates 900 travel records
#   python manage.py seed_data --count 500
#   python manage.py seed_data --clear  # wipes travel data first, then seeds

import random
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils.timezone import now


# ── Realistic BISU travel data pools ─────────────────────────────────

DESTINATIONS = [
    # Bohol (local)
    'Tagbilaran City, Bohol',
    'Panglao, Bohol',
    'Loboc, Bohol',
    'Jagna, Bohol',
    'Ubay, Bohol',
    'Talibon, Bohol',
    'Candijay, Bohol',
    'Bilar, Bohol',
    'Carmen, Bohol',
    'Dauis, Bohol',
    'Corella, Bohol',
    'Dimiao, Bohol',
    'Loon, Bohol',
    'Alburquerque, Bohol',
    # Out of province
    'Cebu City, Cebu',
    'Mandaue City, Cebu',
    'Lapu-Lapu City, Cebu',
    'Manila, Metro Manila',
    'Quezon City, Metro Manila',
    'Pasay City, Metro Manila',
    'Makati City, Metro Manila',
    'Davao City, Davao del Sur',
    'Iloilo City, Iloilo',
    'Bacolod City, Negros Occidental',
    'Dumaguete City, Negros Oriental',
    'Cagayan de Oro City, Misamis Oriental',
    'Zamboanga City, Zamboanga del Sur',
    'General Santos City, South Cotabato',
]

OUT_OF_PROVINCE_DESTINATIONS = [
    'Cebu City, Cebu', 'Mandaue City, Cebu', 'Lapu-Lapu City, Cebu',
    'Manila, Metro Manila', 'Quezon City, Metro Manila',
    'Pasay City, Metro Manila', 'Makati City, Metro Manila',
    'Davao City, Davao del Sur', 'Iloilo City, Iloilo',
    'Bacolod City, Negros Occidental', 'Dumaguete City, Negros Oriental',
    'Cagayan de Oro City, Misamis Oriental',
]

PURPOSES = [
    'To attend the Regional Training on Outcome-Based Education',
    'To participate in the CHED Regional Conference on Higher Education',
    'To attend the Workshop on Research and Development',
    'To represent the university in the Inter-Campus Sports Festival',
    'To attend the National Conference on Technical-Vocational Education',
    'To participate in the Regional Skills Competition',
    'To attend the Seminar on Financial Management and Budgeting',
    'To represent the college in the Regional Academic Excellence Awards',
    'To attend the Workshop on Curriculum Development and Enhancement',
    'To participate in the National Summit on Sustainable Development',
    'To attend the Training on Gender and Development Mainstreaming',
    'To represent the campus in the Regional Cultural Festival',
    'To attend the Seminar-Workshop on Disaster Risk Reduction',
    'To participate in the Regional Research Colloquium',
    'To attend the National Conference on Information Technology',
    'To represent BISU in the Inter-University Debate Competition',
    'To attend the Workshop on Student Affairs and Services',
    'To participate in the Regional Conference on Environmental Management',
    'To attend the Seminar on Human Resource Management',
    'To represent the university in the PASUC Annual Convention',
    'To attend the submission of documents at CHED Regional Office',
    'To attend the coordination meeting with partner agencies',
    'To conduct field visit and monitoring of extension activities',
    'To attend the year-end assessment and planning workshop',
    'To participate in the Solidarity Meeting and document submission',
    'To attend the Regional Board Meeting of State Universities and Colleges',
    'To represent the university in the accreditation visit preparation',
    'To attend the benchmarking activity with partner institutions',
    'To participate in the national skills assessment workshop',
    'To attend the regional symposium on academic quality assurance',
]

FIRST_NAMES = [
    'Maria', 'Jose', 'Juan', 'Ana', 'Rosa', 'Pedro', 'Carmen',
    'Manuel', 'Elena', 'Antonio', 'Luz', 'Eduardo', 'Teresita',
    'Roberto', 'Cristina', 'Fernando', 'Maricel', 'Rolando',
    'Lourdes', 'Renato', 'Marites', 'Alfredo', 'Gloria', 'Danilo',
    'Remedios', 'Rodrigo', 'Erlinda', 'Armando', 'Nenita', 'Virgilio',
    'Cynthia', 'Leonardo', 'Florencia', 'Arturo', 'Natividad',
    'Roger', 'Marlina', 'Luzminda', 'Mark', 'Bobby', 'Shirley',
]

LAST_NAMES = [
    'Santos', 'Reyes', 'Cruz', 'Garcia', 'Mendoza', 'Torres',
    'Flores', 'Rivera', 'Gomez', 'Diaz', 'Lopez', 'Martinez',
    'Gonzales', 'Ramos', 'Aquino', 'Bautista', 'Villanueva',
    'Castillo', 'Dela Cruz', 'Fernandez', 'Amolato', 'Pilapil',
    'Machete', 'Curay', 'Uy', 'Tabang', 'Magallanes', 'Bacalso',
    'Galo', 'Pacana', 'Deiparine', 'Montecillo', 'Tumulak',
    'Pepito', 'Degamo', 'Caballero', 'Catubig', 'Inting',
]


def random_date_in_year(year):
    start = date(year, 1, 1)
    end   = date(year, 12, 31)
    delta = end - start
    return start + timedelta(days=random.randint(0, delta.days))


def random_date_range(start):
    """Return (start, end) — 1 to 5 day trip."""
    duration = random.choices([1, 2, 3, 4, 5], weights=[40, 30, 15, 10, 5])[0]
    end = start + timedelta(days=duration - 1)
    return start, end if duration > 1 else None


class Command(BaseCommand):
    help = 'Seed the database with realistic travel records for demo/testing'

    def add_arguments(self, parser):
        parser.add_argument(
            '--count', type=int, default=900,
            help='Number of travel records to create (default: 900)'
        )
        parser.add_argument(
            '--clear', action='store_true',
            help='Clear existing travel data before seeding'
        )

    def handle(self, *args, **options):
        from accounts.models import User, College, Campus
        from travel_app.models import (
            TravelRecord, TravelParticipant, TravelDocument,
            BudgetSource, BudgetUsage, CampusBudgetUsage, Notification
        )

        count = options['count']

        # ── Optional clear ────────────────────────────────────────────
        if options['clear']:
            self.stdout.write('🗑  Clearing existing travel data...')
            Notification.objects.all().delete()
            TravelDocument.objects.all().delete()
            TravelParticipant.objects.all().delete()
            TravelRecord.objects.all().delete()
            BudgetUsage.objects.all().delete()
            CampusBudgetUsage.objects.all().delete()
            BudgetSource.objects.all().delete()
            self.stdout.write(self.style.SUCCESS('   Done.\n'))

        # ── Load existing data ────────────────────────────────────────
        users    = list(User.objects.filter(is_approved=True, is_active=True))
        colleges = list(College.objects.all())
        campuses = list(Campus.objects.all())

        if not users:
            self.stdout.write(self.style.ERROR(
                '❌ No approved users found. Please create and approve users first.'
            ))
            return

        if not colleges:
            self.stdout.write(self.style.ERROR(
                '❌ No colleges found. Please create colleges first.'
            ))
            return

        self.stdout.write(
            f'👥 Found {len(users)} users, '
            f'{len(colleges)} colleges, '
            f'{len(campuses)} campuses'
        )

        # ── Create Budget Sources if none exist ───────────────────────
        budget_sources = list(BudgetSource.objects.filter(is_active=True))
        if not budget_sources:
            self.stdout.write('💰 No budget sources found — creating defaults...')
            years = [2024, 2025, 2026]
            source_defs = [
                ('GAA - MOOE', 'COLLEGE', 150000, 0),
                ('IGP Fund',   'COLLEGE', 80000,  0),
                ('GAA - MOOE', 'CAMPUS',  0,       500000),
                ('Special Fund', 'CAMPUS', 0,      200000),
            ]
            for year in years:
                for name, scope, college_amt, campus_amt in source_defs:
                    bs, _ = BudgetSource.objects.get_or_create(
                        name=name, year=year, scope=scope,
                        defaults={
                            'college_budget_amount': Decimal(str(college_amt)),
                            'campus_budget_amount':  Decimal(str(campus_amt)),
                            'is_active': True,
                        }
                    )
            budget_sources = list(BudgetSource.objects.filter(is_active=True))
            self.stdout.write(self.style.SUCCESS(f'   Created {len(budget_sources)} budget sources.\n'))

        college_sources = [s for s in budget_sources if s.scope == 'COLLEGE']
        campus_sources  = [s for s in budget_sources if s.scope == 'CAMPUS']

        # ── Get or create admin user for budget tagging ───────────────
        admin_users = list(User.objects.filter(role='ADMIN'))
        sec_users   = list(User.objects.filter(role__in=['DEPT_SEC', 'CAMPUS_SEC'], is_approved=True))
        tagger_pool = (sec_users + admin_users) or users

        # ── Distribute records across years ───────────────────────────
        year_weights = {2024: 30, 2025: 45, 2026: 25}
        years_pool   = []
        for yr, wt in year_weights.items():
            years_pool.extend([yr] * wt)

        # ── Create travel records ─────────────────────────────────────
        self.stdout.write(f'✈️  Creating {count} travel records...')

        created  = 0
        skipped  = 0
        batch_tr = []
        batch_tp = []

        for i in range(count):
            # Pick creator
            creator = random.choice(users)

            # Pick year and dates
            year       = random.choice(years_pool)
            start_date = random_date_in_year(year)
            start_date, end_date = random_date_range(start_date)

            # Pick destination
            is_out = random.random() < 0.25  # 25% out of province
            if is_out:
                destination = random.choice(OUT_OF_PROVINCE_DESTINATIONS)
            else:
                destination = random.choice([
                    d for d in DESTINATIONS
                    if d not in OUT_OF_PROVINCE_DESTINATIONS
                ])

            purpose = random.choice(PURPOSES)

            # Determine participants (1–6 people)
            num_participants = random.choices(
                [1, 2, 3, 4, 5, 6],
                weights=[30, 25, 20, 12, 8, 5]
            )[0]

            # Pick participants — try same college for COLLEGE scope
            if creator.college and random.random() < 0.7:
                college_users = [u for u in users if u.college == creator.college]
                if len(college_users) >= num_participants:
                    participants = random.sample(college_users, num_participants)
                else:
                    other_users = [u for u in users if u not in college_users]
                    still_needed = max(0, num_participants - len(college_users))
                    participants = college_users + random.sample(
                        other_users,
                        min(still_needed, len(other_users))  # ✅ cap to available pool size
                    )
            else:
                participants = random.sample(users, min(num_participants, len(users)))

            # Ensure creator is in participants
            if creator not in participants:
                participants[0] = creator

            # Determine scope from colleges
            unique_colleges = set(
                p.college.name for p in participants if p.college
            )
            scope = 'CAMPUS' if len(unique_colleges) > 1 else 'COLLEGE'

            # Budget tagging (70% of records are tagged)
            budget_source    = None
            amount_deducted  = Decimal('0')
            budget_tagged_by = None
            budget_tagged_at = None
            funding_college  = None

            if random.random() < 0.70:
                if scope == 'COLLEGE' and college_sources:
                    # Pick source matching year
                    yr_sources = [s for s in college_sources if s.year == year]
                    budget_source = random.choice(yr_sources or college_sources)
                    amount_deducted = Decimal(str(random.randint(300, 8000)))
                elif scope == 'CAMPUS' and campus_sources:
                    yr_sources = [s for s in campus_sources if s.year == year]
                    budget_source = random.choice(yr_sources or campus_sources)
                    amount_deducted = Decimal(str(random.randint(500, 15000)))

                if budget_source:
                    budget_tagged_by = random.choice(tagger_pool)
                    budget_tagged_at = now()

                    # 20% of campus travels are routed to a college
                    if scope == 'CAMPUS' and random.random() < 0.20 and colleges:
                        funding_college = random.choice(colleges)

            tr = TravelRecord(
                destination        = destination,
                start_date         = start_date,
                end_date           = end_date,
                purpose            = purpose,
                is_out_of_province = is_out,
                scope              = scope,
                created_by         = creator,
                budget_source      = budget_source,
                amount_deducted    = amount_deducted,
                budget_tagged_by   = budget_tagged_by,
                budget_tagged_at   = budget_tagged_at,
                funding_college    = funding_college,
                notes              = '',
            )
            batch_tr.append((tr, participants))

            # Progress indicator
            if (i + 1) % 100 == 0:
                self.stdout.write(f'   ... {i + 1}/{count}')

        # ── Bulk create TravelRecords ─────────────────────────────────
        # ── Bulk create TravelRecords ─────────────────────────────────
        self.stdout.write('   Saving travel records...')
        TravelRecord.objects.bulk_create(
            [t for t, _ in batch_tr],
            batch_size=200
        )
        # ✅ Re-fetch to guarantee all PKs are present
        saved_records = list(TravelRecord.objects.order_by('-id')[:len(batch_tr)])
        saved_records.reverse()

        # ── Create TravelParticipants ─────────────────────────────────
        # ── Create TravelParticipants ─────────────────────────────────
        self.stdout.write('   Creating participants...')
        participant_objs = []
        participants_list = [p for _, p in batch_tr]
        for tr, participants in zip(saved_records, participants_list):
            if not tr.pk:  # ✅ skip unsaved records
                continue
            for user in participants:
                participant_objs.append(TravelParticipant(
                    travel_record    = tr,
                    user             = user,
                    college_snapshot = user.college.name if user.college else '',
                    campus_snapshot  = user.campus.name  if user.campus  else '',
                ))

        TravelParticipant.objects.bulk_create(
            participant_objs,
            ignore_conflicts=True,
            batch_size=500
        )

        # ── Update BudgetUsage totals ─────────────────────────────────
        self.stdout.write('   Updating budget usage totals...')
        _rebuild_budget_usage(saved_records, colleges, campuses)

        # ── Summary ───────────────────────────────────────────────────
        total_participants = TravelParticipant.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f'\n✅ Done!\n'
            f'   Travel records created : {created}\n'
            f'   Total participants     : {total_participants}\n'
            f'   Budget sources         : {len(budget_sources)}\n'
        ))

        # ── Stats breakdown ───────────────────────────────────────────
        from django.db.models import Count
        self.stdout.write('📊 Breakdown by year:')
        for yr in [2024, 2025, 2026]:
            c = TravelRecord.objects.filter(start_date__year=yr).count()
            self.stdout.write(f'   {yr}: {c} records')

        self.stdout.write('\n📊 Breakdown by scope:')
        for scope in ['COLLEGE', 'CAMPUS']:
            c = TravelRecord.objects.filter(scope=scope).count()
            self.stdout.write(f'   {scope}: {c} records')

        tagged   = TravelRecord.objects.filter(budget_source__isnull=False).count()
        untagged = TravelRecord.objects.filter(budget_source__isnull=True).count()
        self.stdout.write(f'\n💰 Budget tagged  : {tagged}')
        self.stdout.write(f'   Budget untagged : {untagged}')


def _rebuild_budget_usage(travel_records, colleges, campuses):
    from travel_app.models import TravelRecord, BudgetSource, BudgetUsage, CampusBudgetUsage  # ✅ added TravelRecord
    from django.db.models import Sum
    from accounts.models import College, Campus
    # College budget usage
    college_agg = (
        TravelRecord.objects
        .filter(
            scope='COLLEGE',
            budget_source__isnull=False,
            budget_source__scope='COLLEGE',
        )
        .values(
            'participants__college_snapshot',
            'budget_source',
            'start_date__year',
        )
        .annotate(total=Sum('amount_deducted'))
    )

    for row in college_agg:
        college_name = row['participants__college_snapshot']
        if not college_name:
            continue
        try:
            college = College.objects.get(name=college_name)
            source  = BudgetSource.objects.get(id=row['budget_source'])
            year    = row['start_date__year']
            total   = row['total'] or Decimal('0')

            usage, _ = BudgetUsage.objects.get_or_create(
                college=college, budget_source=source, year=year,
                defaults={'allocated_amount': source.college_budget_amount}
            )
            usage.used_amount = total
            usage.save(update_fields=['used_amount'])
        except Exception:
            continue

    # Campus budget usage
    if campuses:
        campus_agg = (
            TravelRecord.objects
            .filter(
                scope='CAMPUS',
                budget_source__isnull=False,
                budget_source__scope='CAMPUS',
            )
            .values(
                'participants__campus_snapshot',
                'budget_source',
                'start_date__year',
            )
            .annotate(total=Sum('amount_deducted'))
        )

        for row in campus_agg:
            campus_name = row['participants__campus_snapshot']
            if not campus_name:
                continue
            try:
                campus = Campus.objects.get(name=campus_name)
                source = BudgetSource.objects.get(id=row['budget_source'])
                year   = row['start_date__year']
                total  = row['total'] or Decimal('0')

                usage, _ = CampusBudgetUsage.objects.get_or_create(
                    campus=campus, budget_source=source, year=year,
                    defaults={'allocated_amount': source.campus_budget_amount}
                )
                usage.used_amount = total
                usage.save(update_fields=['used_amount'])
            except Exception:
                continue