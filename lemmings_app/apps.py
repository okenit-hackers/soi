import logging

from django.apps import AppConfig
from django.utils.translation import gettext_lazy

logger = logging.getLogger(__name__)


class LemmingsAppConfig(AppConfig):
 name = 'lemmings_app'
 verbose_name = gettext_lazy('Lemmings app')

 def ready(self):
  import lemmings_app.signals as signal
  logger.info(f'loaded {signal}')