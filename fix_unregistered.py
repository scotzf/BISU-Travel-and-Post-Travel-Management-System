import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'travel_mgmt.settings')
django.setup()

from django.db import connection

print("Adding unregistered_travelers column...")
with connection.cursor() as cursor:
    try:
        cursor.execute("""
            ALTER TABLE travel_app_travelrecord
            ADD COLUMN unregistered_travelers LONGTEXT NULL
        """)
        print("OK: Column added")
    except Exception as e:
        print(f"SKIP: {e}")

print("\nVerifying column exists:")
with connection.cursor() as cursor:
    cursor.execute("DESCRIBE travel_app_travelrecord")
    for row in cursor.fetchall():
        if 'unregistered' in row[0]:
            print(f"FOUND: {row}")

print("\nDone!")