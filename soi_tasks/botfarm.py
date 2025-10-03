import logging
import os

import logstash
from celery import Celery
from celery.signals import after_setup_logger, after_setup_task_logger

from soi_app import settings

# Настройка celery для внутренних задач фермы ботов (без работы в интернете).
# Логи тоже будут внутренними.

logger = logging.getLogger(__name__)


def initialize_logstash(logger=None, loglevel=logging.DEBUG, **kwargs):
 handler = logstash.TCPLogstashHandler(
  settings.LOGSTASH_INTERNAL_CONF['host'],
  settings.LOGSTASH_INTERNAL_CONF['port'],
  tags=['worker', 'botfarm']
 )
 handler.setLevel(loglevel)
 logger.addHandler(handler)
 # logger.setLevel(logging.DEBUG)
 return logger


try:
 # Если soi установлен в sos
 # noinspection PyPackageRequirements,PyUnresolvedReferences
 from sos_tasks.botfarm import app
except (ImportError, ModuleNotFoundError):
 # Если soi работает сам по себе
 os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'soi_app.settings')
 initialize_logstash = after_setup_logger.connect(after_setup_task_logger.connect(initialize_logstash))
 app = Celery(
  'soi_tasks.botfarm',
  broker=f'redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_BROCKER_DATABASE_NUMBER}',
  backend=f'redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_BACKEND_DATABASE_NUMBER}'
 )
 app.conf.update({
  'task_routes': ('soi_tasks.routing.BotfarmTaskRouter',),
 })
 app.conf.ONCE = {
  'backend': 'celery_once.backends.Redis',
  'settings': {
   'url': f'redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_BACKEND_DATABASE_NUMBER}',
   'default_timeout': 60 * 60 * 10
  }
 }
 app.conf.task_acks_late = True