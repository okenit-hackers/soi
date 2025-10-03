from django.core.management.base import BaseCommand

from soi_app.settings import INTERNAL_CELERY_QUEUE_NAME
from soi_tasks.internal import app


class Command(BaseCommand):
 help = 'Start internal celery worker'

 def handle(self, *args, **options):
  app.start([
   'celery', 'worker',
   '-A', 'soi_tasks.internal',
   '-l', 'info', '-Q', options['queue']
  ])

 def add_arguments(self, parser):
  parser.add_argument(
   '-q',
   '--queue',
   action='store',
   default=INTERNAL_CELERY_QUEUE_NAME,
   help='Specify queue'
  )