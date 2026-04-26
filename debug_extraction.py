import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'travel_mgmt.settings')
django.setup()

from travel_app.models import TravelRecord, ParticipantDocument
from travel_app.ai_service import extract_from_document
import json

# Change this to your actual travel ID
TRAVEL_ID = 2749

travel = TravelRecord.objects.get(id=TRAVEL_ID)
docs = ParticipantDocument.objects.filter(
    participant__travel_record=travel,
    doc_type='TRAVEL_ORDER',
)

print(f"Found {docs.count()} travel order doc(s) for travel {TRAVEL_ID}")
print("=" * 60)

for doc in docs:
    print(f"Doc ID: {doc.id}")
    print(f"extraction_successful: {doc.extraction_successful}")
    print(f"extraction_raw: {doc.extraction_raw[:200] if doc.extraction_raw else 'EMPTY'}")
    print(f"File: {doc.file.name}")
    print()

# Try running extraction on the first doc
first_doc = docs.first()
if first_doc:
    print("=" * 60)
    print(f"Running extraction on doc {first_doc.id}...")
    try:
        extract_from_document(first_doc)
        first_doc.refresh_from_db()
        print(f"extraction_successful: {first_doc.extraction_successful}")
        print(f"extraction_raw: {first_doc.extraction_raw[:500] if first_doc.extraction_raw else 'EMPTY'}")
        if first_doc.extraction_raw:
            data = json.loads(first_doc.extraction_raw)
            print(f"traveler_names: {data.get('traveler_names', [])}")
    except Exception as e:
        print(f"ERROR: {e}")