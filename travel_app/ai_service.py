
# travel_app/ai_service.py
# Full document extraction service using Ollama llama3.2:3b
# Handles: PDF, DOCX, XLSX, Images (JPG, PNG), and plain text

import os
import json
import base64
import requests
import logging
import pytesseract
import platform
if platform.system() == 'Windows':
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

logger = logging.getLogger(__name__)

OLLAMA_URL  = 'http://localhost:11434/api/generate'
OLLAMA_MODEL = 'llama3.2:3b'


# ══════════════════════════════════════════════════════════════════════
# TEXT EXTRACTION — get raw text from any file type
# ══════════════════════════════════════════════════════════════════════

def extract_text_from_file(file_path):
    """
    Extract raw text from any supported file type.
    Returns (text, method) where method describes how it was extracted.
    """
    ext = os.path.splitext(file_path)[1].lower()

    # ── PDF ──────────────────────────────────────────────────────────
    if ext == '.pdf':
        return _extract_from_pdf(file_path)

    # ── DOCX ─────────────────────────────────────────────────────────
    elif ext in ['.docx', '.doc']:
        return _extract_from_docx(file_path)

    # ── XLSX / XLS ───────────────────────────────────────────────────
    elif ext in ['.xlsx', '.xls']:
        return _extract_from_xlsx(file_path)

    # ── Images ───────────────────────────────────────────────────────
    elif ext in ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp']:
        return _extract_from_image(file_path)

    # ── Plain text ───────────────────────────────────────────────────
    elif ext in ['.txt', '.csv']:
        return _extract_from_text(file_path)

    else:
        return None, 'unsupported'


def _extract_from_pdf(file_path):
    """Try text extraction first, fall back to OCR if scanned."""
    try:
        import PyPDF2
        text = ''
        with open(file_path, 'rb') as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                text += page.extract_text() or ''

        if len(text.strip()) > 50:
            return text.strip(), 'pdf_text'

        # Scanned PDF — use OCR via pdf2image
        return _ocr_pdf(file_path)

    except Exception as e:
        logger.error(f"PDF extraction error: {e}")
        return None, 'error'


def _ocr_pdf(file_path):
    """Convert PDF pages to images then OCR them."""
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
        text = '\n'.join([para.text for para in doc.paragraphs if para.text.strip()])

        # Also extract from tables
        for table in doc.tables:
            for row in table.rows:
                row_text = ' | '.join([cell.text.strip() for cell in row.cells if cell.text.strip()])
                if row_text:
                    text += '\n' + row_text

        return text.strip(), 'docx'
    except Exception as e:
        logger.error(f"DOCX extraction error: {e}")
        return None, 'error'


def _extract_from_xlsx(file_path):
    try:
        import openpyxl
        wb   = openpyxl.load_workbook(file_path, data_only=True)
        text = ''
        for sheet in wb.worksheets:
            text += f"\n[Sheet: {sheet.title}]\n"
            for row in sheet.iter_rows(values_only=True):
                row_text = ' | '.join([str(v) for v in row if v is not None])
                if row_text.strip():
                    text += row_text + '\n'

        return text.strip(), 'xlsx'
    except Exception as e:
        logger.error(f"XLSX extraction error: {e}")
        return None, 'error'


def _extract_from_image(file_path):
    """Use pytesseract OCR on images."""
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
# OLLAMA AI ANALYSIS
# ══════════════════════════════════════════════════════════════════════

def analyze_with_ollama(text, doc_type_label):
    """
    Send extracted text to Ollama and get structured data back.
    Returns a dict with extracted fields.
    """
    if not text or len(text.strip()) < 10:
        return None

    # Truncate very long documents to avoid token limits
    text = text[:3000] if len(text) > 3000 else text

    prompt = f"""You are analyzing a Philippine government travel document from Bohol Island State University (BISU).

Document type: {doc_type_label}

Document content:
---
{text}
---

Extract the following information from this document. If a field is not found, use null.
Respond ONLY with a valid JSON object, no explanation, no markdown, no extra text.

{{
    "is_travel_related": true or false,
    "document_type_detected": "what type of document this appears to be",
    "destination": "city or place name or null",
    "start_date": "YYYY-MM-DD or null",
    "end_date": "YYYY-MM-DD or null",
    "amount": numeric value only or null,
    "purpose": "brief description or null",
    "num_travelers": numeric value or null,
    "traveler_names": ["name1", "name2"] or [],
    "confidence": "high", "medium", or "low"
}}"""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                'model':  OLLAMA_MODEL,
                'prompt': prompt,
                'stream': False,
            },
            timeout=60
        )

        if response.status_code != 200:
            logger.error(f"Ollama error: {response.status_code}")
            return None

        raw = response.json().get('response', '')

        # Clean up response — extract JSON
        raw = raw.strip()
        if '```' in raw:
            raw = raw.split('```')[1]
            if raw.startswith('json'):
                raw = raw[4:]
        raw = raw.strip()

        # Find JSON object
        start = raw.find('{')
        end   = raw.rfind('}') + 1
        if start >= 0 and end > start:
            raw = raw[start:end]

        return json.loads(raw)

    except json.JSONDecodeError as e:
        logger.error(f"Ollama JSON parse error: {e}")
        return None
    except requests.exceptions.Timeout:
        logger.error("Ollama timeout")
        return None
    except Exception as e:
        logger.error(f"Ollama error: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT — called after document upload
# ══════════════════════════════════════════════════════════════════════

def extract_from_document(travel_document):
    """
    Main function called after a TravelDocument is uploaded.
    Updates the document's extracted fields in the database.

    Usage in views.py upload_document view:
        from .ai_service import extract_from_document
        extract_from_document(doc)
    """
    from .models import TravelDocument

    doc = travel_document

    try:
        file_path = doc.file.path
    except Exception:
        logger.error(f"Cannot get file path for doc {doc.id}")
        return

    # Mark extraction as attempted
    doc.extraction_attempted = True
    doc.save(update_fields=['extraction_attempted'])

    # Step 1 — Extract text from file
    text, method = extract_text_from_file(file_path)

    if not text:
        doc.extraction_raw = json.dumps({'error': 'Could not extract text', 'method': method})
        doc.extraction_successful = False
        doc.save(update_fields=['extraction_raw', 'extraction_successful'])
        return

    # Step 2 — Send to Ollama for analysis
    doc_type_label = doc.get_doc_type_display()
    result = analyze_with_ollama(text, doc_type_label)

    if not result:
        doc.extraction_raw = json.dumps({'error': 'Ollama did not return valid JSON', 'text_preview': text[:200]})
        doc.extraction_successful = False
        doc.save(update_fields=['extraction_raw', 'extraction_successful'])
        return

    # Step 3 — Save extracted fields
    doc.extraction_raw       = json.dumps(result)
    doc.extraction_successful = True

    # Map result fields to model fields
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
            from decimal import Decimal
            doc.extracted_amount = Decimal(str(result['amount']))
        except Exception:
            pass

    # Parse dates safely
    if result.get('start_date'):
        try:
            from datetime import datetime
            doc.extracted_start_date = datetime.strptime(
                str(result['start_date']), '%Y-%m-%d'
            ).date()
        except ValueError:
            pass

    if result.get('end_date'):
        try:
            from datetime import datetime
            doc.extracted_end_date = datetime.strptime(
                str(result['end_date']), '%Y-%m-%d'
            ).date()
        except ValueError:
            pass

    # Flag non-travel documents
    if result.get('is_travel_related') is False:
        doc.notes = (doc.notes or '') + ' [AI: This document may not be travel-related]'

    doc.save(update_fields=[
        'extraction_raw', 'extraction_successful',
        'extracted_destination', 'extracted_purpose',
        'extracted_num_travelers', 'extracted_amount',
        'extracted_start_date', 'extracted_end_date',
        'notes',
    ])

    logger.info(f"Extraction complete for doc {doc.id} — confidence: {result.get('confidence')}")


# ══════════════════════════════════════════════════════════════════════
# BACKGROUND EXTRACTION — runs in a thread so upload doesn't block
# ══════════════════════════════════════════════════════════════════════

def extract_from_document_async(travel_document_id):
    """
    Run extraction in a background thread so the user's upload
    response is instant. Ollama can take 5-30 seconds.

    Usage:
        import threading
        from .ai_service import extract_from_document_async
        t = threading.Thread(target=extract_from_document_async, args=(doc.id,))
        t.daemon = True
        t.start()
    """
    from .models import TravelDocument
    try:
        doc = TravelDocument.objects.get(id=travel_document_id)
        extract_from_document(doc)
    except TravelDocument.DoesNotExist:
        logger.error(f"TravelDocument {travel_document_id} not found for async extraction")
    except Exception as e:
        logger.error(f"Async extraction error: {e}")