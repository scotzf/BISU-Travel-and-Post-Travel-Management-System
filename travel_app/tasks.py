# travel_app/tasks.py
from celery import shared_task
import logging

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def extract_document_task(self, document_id):
    """
    Celery task — runs AI extraction in background.
    Retries up to 3 times on failure with 10s delay.
    """
    from .models import ParticipantDocument
    from .ai_service import extract_from_document

    try:
        doc = ParticipantDocument.objects.get(id=document_id)
        extract_from_document(doc)
    except ParticipantDocument.DoesNotExist:
        logger.error(f"ParticipantDocument {document_id} not found")
    except Exception as exc:
        logger.error(f"Task failed for doc {document_id}: {exc}")
        raise self.retry(exc=exc)