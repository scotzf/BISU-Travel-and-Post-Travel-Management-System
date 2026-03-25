# travel_app/ai_service.py
import os
import re
import json
import requests
import logging
import platform

logger = logging.getLogger(__name__)

OLLAMA_URL    = 'http://localhost:11434/api/generate'
OLLAMA_MODEL  = 'llama3.2:3b'
OLLAMA_TIMEOUT = 60

if platform.system() == 'Windows':
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'


# ══════════════════════════════════════════════════════════════════════
# KEYWORD CLASSIFICATION MAP
# ══════════════════════════════════════════════════════════════════════

KEYWORD_MAP = {
    'TRAVEL_ORDER': [
        'travel order', 'office order', 'special order',
        'is hereby directed', 'is hereby authorized to travel',
    ],
    'ITINERARY': [
        'itinerary of travel', 'actual itinerary',
        'itinerary of activities', 'schedule of activities',
    ],
    'DV': [
        'disbursement voucher', 'disb. voucher', 'dv no',
        'dv number', 'disbursement  voucher',
    ],
    'BURS': [
        'budget utilization', 'burs', 'allotment class',
        'uacs code', 'budget utilization request',
    ],
    'CERTIFICATE': [
        'certificate of appearance', 'certificate of travel',
        'certificate of attendance', 'this is to certify',
        'appeared before', 'has attended', 'certificate of travel completion',
    ],
    'RECEIPTS': [
        'official receipt', 'or no', 'o.r. no',
        'cash invoice', 'acknowledgement receipt',
    ],
    'POST_REPORT': [
        'post-activity report', 'post activity report',
        'accomplishment report', 'travel report',
    ],
    'LETTER_REQUEST': [
        'letter request', 'request for authority',
        'respectfully request', 'permission to travel',
    ],
}

# Map doc types to sheet name keywords for multi-sheet XLSX
XLSX_SHEET_MAP = {
    'TRAVEL_ORDER': ['travel order', 'to', 'order'],
    'ITINERARY':    ['it', 'itinerary'],
    'DV':           ['dv', 'disbursement'],
    'BURS':         ['burs', 'budget utilization'],
    'CERTIFICATE':  ['cert', 'certification', 'ctc'],
    'RECEIPTS':     ['receipt', 'or'],
    'POST_REPORT':  ['report', 'post'],
    'LETTER_REQUEST': ['letter', 'request'],
}


# ══════════════════════════════════════════════════════════════════════
# TEXT EXTRACTION
# ══════════════════════════════════════════════════════════════════════

def extract_text_from_file(file_path, doc_type=None):
    ext = os.path.splitext(file_path)[1].lower()

    if ext == '.pdf':
        return _extract_from_pdf(file_path)
    elif ext in ['.docx', '.doc']:
        return _extract_from_docx(file_path)
    elif ext in ['.xlsx', '.xls']:
        return _extract_from_xlsx(file_path, doc_type=doc_type)
    elif ext in ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp']:
        return _extract_from_image(file_path)
    elif ext in ['.txt', '.csv']:
        return _extract_from_text(file_path)
    else:
        return None, 'unsupported'


def _find_best_sheet(wb, doc_type):
    """
    For multi-sheet XLSX files, find the sheet that matches
    the document type being extracted.
    Returns the best matching worksheet.
    """
    if not doc_type or doc_type not in XLSX_SHEET_MAP:
        return wb.active

    keywords = XLSX_SHEET_MAP[doc_type]
    sheet_names = wb.sheetnames

    for kw in keywords:
        for name in sheet_names:
            if kw.lower() in name.lower():
                logger.info(f"XLSX: matched sheet '{name}' for doc_type {doc_type}")
                return wb[name]

    # No match — fall back to active sheet
    logger.info(f"XLSX: no sheet match for {doc_type}, using active sheet")
    return wb.active


def _extract_from_xlsx(file_path, doc_type=None):
    try:
        import openpyxl
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)

        # Pick the right sheet based on doc_type
        ws = _find_best_sheet(wb, doc_type)

        text = f'[Sheet: {ws.title}]\n'
        for row in ws.iter_rows(max_row=60, values_only=True):
            row_text = ' | '.join([
                str(v).strip() for v in row
                if v is not None and str(v).strip()
            ])
            if row_text.strip():
                text += row_text + '\n'

        return text.strip(), 'xlsx'
    except Exception as e:
        logger.error(f"XLSX extraction error: {e}")
        return None, 'error'


def _extract_from_pdf(file_path):
    try:
        import PyPDF2
        text = ''
        with open(file_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text += page.extract_text() or ''
        if len(text.strip()) > 50:
            return text.strip(), 'pdf_text'
        return _ocr_pdf(file_path)
    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        return None, 'error'


def _ocr_pdf(file_path):
    try:
        from pdf2image import convert_from_path
        import pytesseract
        pages = convert_from_path(file_path, dpi=200)
        text  = ''
        for page in pages:
            text += pytesseract.image_to_string(page, lang='eng') + '\n'
        return text.strip(), 'pdf_ocr'
    except Exception as e:
        logger.error(f"PDF OCR error: {e}")
        return None, 'error'


def _extract_from_docx(file_path):
    try:
        from docx import Document
        doc  = Document(file_path)
        text = '\n'.join([p.text for p in doc.paragraphs if p.text.strip()])
        for table in doc.tables:
            for row in table.rows:
                row_text = ' | '.join([c.text.strip() for c in row.cells if c.text.strip()])
                if row_text:
                    text += '\n' + row_text
        return text.strip(), 'docx'
    except Exception as e:
        logger.error(f"DOCX extraction error: {e}")
        return None, 'error'


def _extract_from_image(file_path):
    try:
        import pytesseract
        from PIL import Image
        img  = Image.open(file_path)
        text = pytesseract.image_to_string(img, lang='eng')
        return text.strip(), 'image_ocr'
    except Exception as e:
        logger.error(f"Image OCR error: {e}")
        return None, 'error'


def _extract_from_text(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read().strip(), 'text'
    except Exception as e:
        logger.error(f"Text extraction error: {e}")
        return None, 'error'


# ══════════════════════════════════════════════════════════════════════
# SMART PREPROCESSOR — cleans raw text before sending to Ollama
# Extracts key fields using Python (fast, free, accurate)
# Ollama only needs to format the result as JSON
# ══════════════════════════════════════════════════════════════════════

MONTH_PATTERN = (
    r'(January|February|March|April|May|June|'
    r'July|August|September|October|November|December)'
    r'\s+\d{1,2},?\s+\d{4}'
)

CITY_PATTERN = (
    r'(Tagbilaran|Cebu|Manila|Davao|Cagayan de Oro|'
    r'Dumaguete|Bacolod|Iloilo|Zamboanga|Bohol|'
    r'Candijay|Bilar|Jagna|Panglao|Ubay|Talibon)'
    r'(?:\s*City)?'
)

AMOUNT_PATTERN = r'(?:PHP|Php|₱|P)?\s*([\d,]+(?:\.\d{2})?)'


def preprocess_text(text, doc_type):
    """
    Convert raw extracted text into clean labeled fields.
    This does the heavy lifting so Ollama only formats JSON.
    Returns a clean string like:
        PAYEE: Roger E. Amolato
        AMOUNT: 540.00
        PURPOSE: Reimbursement of travelling expenses...
    """
    hints = {}
    lines = [l.strip() for l in text.split('\n') if l.strip()]

    for i, line in enumerate(lines):
        low = line.lower()

        # ── Payee ────────────────────────────────────────────────────
        if 'payee' in low and '|' in line:
            parts = [p.strip() for p in line.split('|')]
            for p in parts[1:]:
                if p and 'tin' not in p.lower() and 'ors' not in p.lower():
                    hints.setdefault('payee', p)
                    break

        # ── Purpose (long meaningful lines) ─────────────────────────
        if len(line) > 60 and any(w in low for w in [
            'travel', 'attend', 'reimburse', 'participate',
            'training', 'seminar', 'meeting', 'conference', 'workshop'
        ]):
            hints.setdefault('purpose', line.strip().split('|')[0].strip())

        # ── Amount ───────────────────────────────────────────────────
        if any(w in low for w in ['amount due', 'total amount', 'php', '₱']):
            amounts = re.findall(r'\b(\d[\d,]*(?:\.\d{2})?)\b', line)
            # Filter out years and tiny numbers
            amounts = [a for a in amounts if not re.match(r'^20\d{2}$', a) and float(a.replace(',','')) > 10]
            if amounts:
                hints.setdefault('amount', amounts[-1].replace(',', ''))

        # ── Amount in words ──────────────────────────────────────────
        if 'pesos only' in low:
            hints['amount_in_words'] = line.split('|')[-1].strip()

        # ── Dates ────────────────────────────────────────────────────
        dates = re.findall(MONTH_PATTERN, line)
        if dates and 'date_mentioned' not in hints:
            full = re.search(MONTH_PATTERN, line)
            if full:
                hints['date_mentioned'] = full.group(0)

        # ── Destination / City ───────────────────────────────────────
        city = re.search(CITY_PATTERN, line, re.IGNORECASE)
        if city:
            hints.setdefault('destination', city.group(0).strip())

        # ── Traveler name (for TO / CERTIFICATE) ────────────────────
        if any(w in low for w in ['name :', 'name:', 'traveler']):
            parts = [p.strip() for p in line.split('|')]
            for p in parts[1:]:
                if p and len(p) > 3 and p.replace(' ','').isalpha():
                    hints.setdefault('traveler_name', p)
                    break

        # ── BURS/DV number ───────────────────────────────────────────
        if 'dv no' in low or 'dv number' in low:
            nums = re.findall(r'[\w\-]+', line.split(':')[-1])
            if nums:
                hints.setdefault('dv_number', nums[0])

        if 'burs' in low and ('no' in low or 'number' in low):
            nums = re.findall(r'[\w\-]+', line.split(':')[-1])
            if nums:
                hints.setdefault('burs_number', nums[0])

    # ── Build clean labeled text ─────────────────────────────────────
    doc_label = doc_type.replace('_', ' ')
    clean = f'DOCUMENT TYPE: {doc_label}\n'
    clean += 'ENTITY: Bohol Island State University\n'

    field_labels = {
        'payee':           'PAYEE',
        'traveler_name':   'TRAVELER NAME',
        'destination':     'DESTINATION',
        'purpose':         'PURPOSE',
        'amount':          'AMOUNT (PHP)',
        'amount_in_words': 'AMOUNT IN WORDS',
        'date_mentioned':  'DATE',
        'dv_number':       'DV NUMBER',
        'burs_number':     'BURS NUMBER',
    }

    for key, label in field_labels.items():
        if key in hints:
            clean += f'{label}: {hints[key]}\n'

    return clean, hints


# ══════════════════════════════════════════════════════════════════════
# STAGE 1 — CLASSIFICATION
# ══════════════════════════════════════════════════════════════════════

def classify_document(text, user_selected_type=None):
    """
    Keyword match first, Ollama fallback, user selection last resort.
    Returns (detected_type, confidence)
    """
    if not text:
        return user_selected_type or 'UNKNOWN', 'low'

    title_area = text[:400].lower()

    for doc_type, keywords in KEYWORD_MAP.items():
        for kw in keywords:
            if kw in title_area:
                logger.info(f"Classified as {doc_type} via keyword '{kw}'")
                return doc_type, 'high'

    ollama_type = _classify_with_ollama(text[:600])
    if ollama_type:
        return ollama_type, 'medium'

    if user_selected_type:
        return user_selected_type, 'low'

    return 'UNKNOWN', 'low'


def _classify_with_ollama(snippet):
    valid = list(KEYWORD_MAP.keys())
    prompt = f"""Identify this Philippine government travel document type.

Document beginning:
---
{snippet}
---

Reply with ONLY one of these exact values:
TRAVEL_ORDER, ITINERARY, DV, BURS, CERTIFICATE, RECEIPTS, POST_REPORT, LETTER_REQUEST, UNKNOWN"""

    try:
        r = requests.post(
            OLLAMA_URL,
            json={'model': OLLAMA_MODEL, 'prompt': prompt, 'stream': False},
            timeout=30
        )
        if r.status_code != 200:
            return None
        raw = r.json().get('response', '').strip().upper().split()[0]
        raw = re.sub(r'[^A-Z_]', '', raw)
        return raw if raw in valid else None
    except Exception as e:
        logger.error(f"Ollama classify error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# STAGE 2 — EXTRACTION SCHEMAS PER DOC TYPE
# ══════════════════════════════════════════════════════════════════════

EXTRACTION_SCHEMAS = {
    'TRAVEL_ORDER': {
        'fields': ['destination', 'start_date', 'end_date', 'purpose', 'num_travelers', 'traveler_names'],
        'schema': '''{
    "destination": "city or place or null",
    "start_date": "YYYY-MM-DD or null",
    "end_date": "YYYY-MM-DD or null",
    "purpose": "reason for travel or null",
    "num_travelers": number or null,
    "traveler_names": ["Full Name"] or [],
    "confidence": "high/medium/low"
}'''
    },
    'ITINERARY': {
        'fields': ['destination', 'start_date', 'end_date', 'purpose'],
        'schema': '''{
    "destination": "city or place or null",
    "start_date": "YYYY-MM-DD or null",
    "end_date": "YYYY-MM-DD or null",
    "purpose": "event or activity name or null",
    "confidence": "high/medium/low"
}'''
    },
    'DV': {
        'fields': ['amount', 'purpose', 'destination', 'start_date', 'end_date'],
        'schema': '''{
    "amount": numeric only or null,
    "purpose": "nature of payment or null",
    "destination": "city or place or null",
    "start_date": "YYYY-MM-DD or null",
    "end_date": "YYYY-MM-DD or null",
    "payee": "payee name or null",
    "dv_number": "DV number or null",
    "confidence": "high/medium/low"
}'''
    },
    'BURS': {
        'fields': ['amount', 'purpose', 'destination', 'start_date', 'end_date'],
        'schema': '''{
    "amount": numeric only or null,
    "purpose": "nature of expense or null",
    "destination": "city or place or null",
    "start_date": "YYYY-MM-DD or null",
    "end_date": "YYYY-MM-DD or null",
    "burs_number": "BURS number or null",
    "allotment_class": "MOOE/CO/PS or null",
    "confidence": "high/medium/low"
}'''
    },
    'CERTIFICATE': {
        'fields': ['destination', 'start_date', 'end_date', 'traveler_names'],
        'schema': '''{
    "destination": "city or place or null",
    "start_date": "YYYY-MM-DD or null",
    "end_date": "YYYY-MM-DD or null",
    "traveler_names": ["Full Name"] or [],
    "num_travelers": number or null,
    "confidence": "high/medium/low"
}'''
    },
    'RECEIPTS': {
        'fields': ['amount', 'start_date'],
        'schema': '''{
    "amount": numeric only or null,
    "start_date": "YYYY-MM-DD or null",
    "vendor": "establishment name or null",
    "confidence": "high/medium/low"
}'''
    },
    'POST_REPORT': {
        'fields': ['destination', 'start_date', 'end_date', 'purpose'],
        'schema': '''{
    "destination": "city or place or null",
    "start_date": "YYYY-MM-DD or null",
    "end_date": "YYYY-MM-DD or null",
    "purpose": "event summary or null",
    "confidence": "high/medium/low"
}'''
    },
    'LETTER_REQUEST': {
        'fields': ['destination', 'start_date', 'end_date', 'purpose'],
        'schema': '''{
    "destination": "city or place or null",
    "start_date": "YYYY-MM-DD or null",
    "end_date": "YYYY-MM-DD or null",
    "purpose": "reason for request or null",
    "num_travelers": number or null,
    "confidence": "high/medium/low"
}'''
    },
}


def _call_ollama_extract(clean_text, doc_type):
    """
    Send preprocessed clean text to Ollama for JSON formatting.
    Since Python already extracted the key fields, Ollama just
    needs to structure and infer dates properly.
    """
    schema = EXTRACTION_SCHEMAS.get(doc_type, EXTRACTION_SCHEMAS['TRAVEL_ORDER'])

    prompt = f"""Extract travel document data and return ONLY a JSON object.

Document data:
---
{clean_text}
---

Rules:
- Convert dates to YYYY-MM-DD format (e.g. "October 30, 2025" → "2025-10-30")
- amount must be numeric only (e.g. 540.00)
- If start_date equals end_date, set both to same date
- Do not guess — use null if not found
- Respond with ONLY the JSON object, no explanation

{schema['schema']}"""

    try:
        r = requests.post(
            OLLAMA_URL,
            json={'model': OLLAMA_MODEL, 'prompt': prompt, 'stream': False},
            timeout=OLLAMA_TIMEOUT
        )
        if r.status_code != 200:
            return None

        raw = r.json().get('response', '').strip()

        if '```' in raw:
            parts = raw.split('```')
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith('json'):
                raw = raw[4:]

        start = raw.find('{')
        end   = raw.rfind('}') + 1
        if start >= 0 and end > start:
            raw = raw[start:end]

        return json.loads(raw)

    except json.JSONDecodeError as e:
        logger.error(f"Ollama JSON parse error: {e}")
        return None
    except requests.exceptions.Timeout:
        logger.error("Ollama extraction timeout")
        return None
    except Exception as e:
        logger.error(f"Ollama extraction error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# PYTHON FALLBACK — if Ollama fails, use Python-extracted hints directly
# ══════════════════════════════════════════════════════════════════════

def _python_fallback(hints, doc_type):
    """
    Build extraction result purely from Python-extracted hints.
    Used when Ollama times out or fails.
    Handles date parsing without needing Ollama.
    """
    from datetime import datetime

    result = {'confidence': 'medium'}

    def parse_date(date_str):
        if not date_str:
            return None
        for fmt in ['%B %d, %Y', '%B %d %Y', '%Y-%m-%d']:
            try:
                return datetime.strptime(date_str.strip(), fmt).strftime('%Y-%m-%d')
            except ValueError:
                continue
        return None

    if 'destination' in hints:
        result['destination'] = hints['destination']
    if 'purpose' in hints:
        result['purpose'] = hints['purpose']
    if 'amount' in hints:
        result['amount'] = hints['amount']
    if 'payee' in hints:
        result['payee'] = hints['payee']
    if 'traveler_name' in hints:
        result['traveler_names'] = [hints['traveler_name']]
        result['num_travelers'] = 1
    if 'dv_number' in hints:
        result['dv_number'] = hints['dv_number']
    if 'burs_number' in hints:
        result['burs_number'] = hints['burs_number']

    # Parse date
    date_str = hints.get('date_mentioned')
    parsed = parse_date(date_str)
    if parsed:
        result['start_date'] = parsed
        result['end_date']   = parsed

    result['_source'] = 'python_fallback'
    return result


# ══════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════

def extract_from_document(travel_document):
    from .models import TravelDocument
    from decimal import Decimal
    from datetime import datetime

    doc = travel_document
    doc.extraction_status    = 'processing'
    doc.extraction_attempted = True
    doc.save(update_fields=['extraction_status', 'extraction_attempted'])

    try:
        file_path = doc.file.path
    except Exception:
        _mark_failed(doc, 'Cannot access file path')
        return

    # ── Step 1: Extract raw text (sheet-aware for XLSX) ──────────────
    text, method = extract_text_from_file(file_path, doc_type=doc.doc_type)

    if not text or len(text.strip()) < 20:
        _mark_failed(doc, f'Could not extract text (method: {method})')
        return

    logger.info(f"Doc {doc.id} — text extracted via {method}, length: {len(text)}")

    # ── Step 2: Classify ─────────────────────────────────────────────
    detected_type, confidence = classify_document(text, user_selected_type=doc.doc_type)
    doc.detected_doc_type     = detected_type
    doc.extraction_confidence = confidence
    doc.save(update_fields=['detected_doc_type', 'extraction_confidence'])
    logger.info(f"Doc {doc.id} — classified as {detected_type} ({confidence})")

    # ── Step 3: Preprocess text → clean labeled fields ───────────────
    clean_text, hints = preprocess_text(text, detected_type)
    logger.info(f"Doc {doc.id} — preprocessed hints: {list(hints.keys())}")

    # ── Step 4: Ollama extraction (with Python fallback) ─────────────
    result = _call_ollama_extract(clean_text, detected_type)

    if not result:
        logger.warning(f"Doc {doc.id} — Ollama failed, using Python fallback")
        result = _python_fallback(hints, detected_type)

    if not result:
        _mark_failed(doc, 'Both Ollama and Python fallback failed')
        return

    # ── Step 5: Save to model fields ─────────────────────────────────
    debug_payload = {
        'method':        method,
        'detected_type': detected_type,
        'confidence':    confidence,
        'user_selected': doc.doc_type,
        'hints':         hints,
        'ollama_result': result,
        'text_preview':  text[:300],
    }
    doc.extraction_raw        = json.dumps(debug_payload, ensure_ascii=False)
    doc.extraction_successful = True
    doc.extraction_status     = 'done'

    if result.get('destination'):
        doc.extracted_destination = str(result['destination'])[:200]
    if result.get('purpose'):
        doc.extracted_purpose = str(result['purpose'])[:500]
    if result.get('num_travelers'):
        try:
            doc.extracted_num_travelers = int(result['num_travelers'])
        except (ValueError, TypeError):
            pass
    if result.get('amount'):
        try:
            doc.extracted_amount = Decimal(str(result['amount']))
        except Exception:
            pass

    for field, model_field in [('start_date', 'extracted_start_date'), ('end_date', 'extracted_end_date')]:
        val = result.get(field)
        if val:
            try:
                setattr(doc, model_field, datetime.strptime(str(val), '%Y-%m-%d').date())
            except ValueError:
                pass

    doc.save(update_fields=[
        'extraction_raw', 'extraction_successful', 'extraction_status',
        'extracted_destination', 'extracted_purpose',
        'extracted_num_travelers', 'extracted_amount',
        'extracted_start_date', 'extracted_end_date',
    ])

    logger.info(f"Doc {doc.id} — done. Source: {result.get('_source', 'ollama')}, "
                f"Confidence: {confidence}, Amount: {result.get('amount')}, "
                f"Destination: {result.get('destination')}")


def _mark_failed(doc, reason):
    doc.extraction_raw        = json.dumps({'error': reason})
    doc.extraction_successful = False
    doc.extraction_status     = 'failed'
    doc.save(update_fields=['extraction_raw', 'extraction_successful', 'extraction_status'])
    logger.error(f"Doc {doc.id} extraction failed: {reason}")