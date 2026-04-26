import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'travel_mgmt.settings')
django.setup()

from django.db import connection

with connection.cursor() as cursor:
    cursor.execute("ALTER TABLE travel_app_budgetusage MODIFY COLUMN user_id BIGINT NULL")
    print("OK: user_id changed to BIGINT")

with connection.cursor() as cursor:
    try:
        cursor.execute("""
            ALTER TABLE travel_app_budgetusage
            ADD CONSTRAINT fk_budgetusage_user
            FOREIGN KEY (user_id) REFERENCES accounts_user(id)
            ON DELETE CASCADE
        """)
        print("OK: FK on user_id added")
    except Exception as e:
        print(f"SKIP FK add: {e}")

print("\nFinal table structure:")
with connection.cursor() as cursor:
    cursor.execute("DESCRIBE travel_app_budgetusage;")
    for row in cursor.fetchall():
        print(row)