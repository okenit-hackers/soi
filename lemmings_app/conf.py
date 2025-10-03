import os

from django.conf import settings
from appconf import AppConf


class LemmingsAppConf(AppConf):
 # todo: выпилить DISABLED_ACTIONS после завершения разработки
 DISABLED_ACTIONS = [a.strip() for a in os.environ.get('LEMMINGS_APP_DISABLED_ACTIONS', '').split(',') if a]
 TEST_USER_NAME = 'SOIANONTEST'

 # задаёт необходимое количество аккаунтов для только что созданной цепочки
 ACCOUNTS_POOL_DEFAULT_VALUES = int(os.environ.get('LEMMINGS_APP_ACCOUNTS_POOL_DEFAULT_VALUES', 1))
 AMOUNT_OF_ATTEMPTS_TO_CREATE_ACCOUNTS = int(
  os.environ.get('LEMMINGS_APP_AMOUNT_OF_ATTEMPTS_TO_CREATE_ACCOUNTS', 10)
 )
 SLEEP_BETWEEN_RUNS = int(os.environ.get('LEMMINGS_APP_SLEEP_BETWEEN_RUNS', 10))
 SLEEP_BETWEEN_RUNS_INSTAGRAM = int(os.environ.get('LEMMINGS_APP_SLEEP_BETWEEN_RUNS_INSTAGRAM', 3600))
 SLEEP_BETWEEN_RUNS_LINKEDIN = int(os.environ.get('LEMMINGS_APP_SLEEP_BETWEEN_RUNS_LINKEDIN', 3600 / 2))
 CELERY_TASK_REGEX = os.environ.get(
  'LEMMINGS_APP_CELERY_TASK_REGEX', '[a-z0-9]{8}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{12}'
 )