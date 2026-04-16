# travel_mgmt/celery.py
import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'travel_mgmt.settings')

app = Celery('travel_mgmt')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()