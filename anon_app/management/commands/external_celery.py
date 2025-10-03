"""
Only for dev
"""

from django.core.management.base import BaseCommand
from soi_tasks.core import app


class Command(BaseCommand):
 help = 'Start internal celery worker'

 def handle(self, *args, **options):
  app.start([
   'celery', 'worker',
   '-A', 'soi_tasks.core',
   '-l', 'info', '-Q', options['queue']
  ])

 def add_arguments(self, parser):
  parser.add_argument(
   '-q',
   '--queue',
   action='store',
   default='external_celery',
   help='Specify queue'
  )