import csv
import datetime
import json
import logging
import random
from hashlib import sha256
from io import TextIOWrapper
from random import randint
from typing import Union

import dill
from celery.canvas import Signature
from django_celery_beat.utils import sign_task_signature
import lemmings
from celery import chain
from lemmings.services_enum import ServiceTaskType, Service
from rest_framework.exceptions import ValidationError
from django_celery_beat.models import PeriodicTask, IntervalSchedule, ClockedSchedule, CrontabSchedule, SolarSchedule

from anon_app.exceptions import MethodNotAvailable
from anon_app.models import Chain
from lemmings_app.conf import settings
from lemmings_app.exceptions import SecurityError, InvalidService
from lemmings_app.models import LemmingsTask, BotAccount
from lemmings_app.tasks import external_app, run_lemmings_task, save_lemmings_result_task
from soi_app.settings import CELERY_TASK_DEFAULT_PRIORITY

logger = logging.getLogger(__name__)


def create_random_password(length: int = 16):
 from string import digits, ascii_lowercase, ascii_uppercase
 random_string = ascii_lowercase * 100 + ascii_uppercase * 100
 digits = digits * 30
 password = f"{random_string}{digits}"
 shuffled_password = (''.join(random.sample(password, len(password))))
 shuffled_password = random.choice(random_string) + shuffled_password
 return shuffled_password[:length]


def _set_lmgs_task_chain(lmgs_task_instance: LemmingsTask) -> bool:
 username = lmgs_task_instance.kwargs['username']

 old_results = [
  r['task_id'] for r in [
   external_app.backend.get_task_meta(task_id=task_id)
   for task_id in LemmingsTask.objects.values_list('task_id', flat=True) if task_id is not None
  ] if r.get('status') == 'SUCCESS' and username in str(r['result'])
 ]

 last_same_task = LemmingsTask.objects.filter(
  task_id__in=old_results,
  action__contains=LemmingsTask.ObjectActionChoice.get_service_name(lmgs_task_instance.action),
  # todo fix get_service_name!
  chain__isnull=False
 ).exclude(id=lmgs_task_instance.id).last()

 if last_same_task is not None:
  lmgs_task_instance.chain = last_same_task.chain
  lmgs_task_instance.save()

 return lmgs_task_instance.chain is not None


def validate_lmgs_task(lmgs_task_instance: LemmingsTask):
 if 'is_internal' in lmgs_task_instance.kwargs:
  raise SecurityError('You can not use `is_internal` option')
 if not hasattr(lemmings.botfarm.Shortcut, lmgs_task_instance.action):
  raise ValidationError(
   f'Неподдерживаемое действие [action = {lmgs_task_instance.action}]'
  )

 # todo: выпилить DISABLED_ACTIONS после завершения разработки
 if lmgs_task_instance.action in settings.LEMMINGS_APP_DISABLED_ACTIONS:
  raise ValidationError('Action is disabled')

 if lmgs_task_instance.action.startswith('login'):
  if lmgs_task_instance.kwargs.get('username') is None \
    or lmgs_task_instance.kwargs.get('password') is None \
    or lmgs_task_instance.kwargs.get('phone_number') is None:
   raise ValidationError(
    f'Недостаточно аргументов для действия {lmgs_task_instance.action}'
   )
  if not _set_lmgs_task_chain(lmgs_task_instance):
   raise ValidationError('Need chain')

 if lmgs_task_instance.action.startswith('create'):
  if set(lmgs_task_instance.kwargs.keys()) - {'init_data'}:
   raise ValidationError(
    f'Слишком много аргументов для действия {lmgs_task_instance.action}'
   )
  if lmgs_task_instance.chain is None:
   raise ValidationError('Need chain')

 if lmgs_task_instance.action.startswith('create'):
  if set(lmgs_task_instance.kwargs.keys()) - {'init_data'}:
   raise ValidationError(
    f'Слишком много аргументов для действия {lmgs_task_instance.action}'
   )
  if lmgs_task_instance.chain is None:
   raise ValidationError('Need chain')


# todo remove this
def create_available_lmgs_tasks(anon_chain: Union[Chain, 'None']):
 def create_and_run(in_method, in_service):
  try:
   lmgs_create_task_instance = LemmingsTask.objects.create(action=in_method.__name__, chain=anon_chain)
   choice_action = LemmingsTask.ObjectActionChoice.get_last_available_method(in_service, ServiceTaskType.LOGIN)
   lmgs_login_task_instance = LemmingsTask.objects.create(
    action=choice_action.__name__,
    chain=anon_chain
   )
  except MethodNotAvailable as e:
   logger.warning(f'{in_service}, {ServiceTaskType.LOGIN}, {e}', exc_info=True)
   return
  except Exception as e:
   logger.error(f'Error creating bots for anonymization chain: {e}', exc_info=True)
   raise ValidationError({
    'error': {
     'code': 3021,
     'description': f'Error creating bots for anonymization chain: {e}'
    }
   })

  tasks_chain = chain(
   run_lemmings_task.s(
    task_identifier=f'lmgs:{lmgs_create_task_instance.id}',
    action=lmgs_create_task_instance.action,
    queue_name=anon_chain.task_queue_name
   ),
   save_lemmings_result_task.s(
    django_task_ids=[lmgs_create_task_instance.id, lmgs_login_task_instance.id],
    task_identifier=f'save:lmgs:{lmgs_create_task_instance.action}:{lmgs_create_task_instance.id}',
    is_internal=True
   ),
   run_lemmings_task.s(
    task_identifier=f'lmgs:{lmgs_login_task_instance.id}',
    action=lmgs_login_task_instance.action,
    queue_name=anon_chain.task_queue_name
   ),
   save_lemmings_result_task.s(
    django_task_ids=[lmgs_create_task_instance.id, lmgs_login_task_instance.id],
    task_identifier=f'save:lmgs:{lmgs_login_task_instance.action}:{lmgs_login_task_instance.id}',
    is_internal=True
   )
  )

  task_id = tasks_chain.apply_async()
  lmgs_create_task_instance.task_id = lmgs_login_task_instance.task_id = task_id

  lmgs_create_task_instance.save()
  lmgs_login_task_instance.save()

 for action_value, _ in LemmingsTask.ObjectActionChoice.choices:
  _, method, _, service, action_type = lemmings.botfarm.Shortcut.resolver(action_value)
  if not action_type == ServiceTaskType.CREATE or method.__name__ in settings.LEMMINGS_APP_DISABLED_ACTIONS:
   continue
  create_and_run(method, service)


def serialize_extra(extra):
 if not(isinstance(extra, list) and extra):
  return extra
 try:
  serialized_json = json.loads(extra[0])
 except json.decoder.JSONDecodeError:
  serialized_json = {}
 return serialized_json


def handle_bots_from_csv(form, in_file, delimiter, create_type=BotAccount.CreateType.IMPORTED.value):
 """Создает записи в BotAccount из считанного содержимого csv файла"""

 f = TextIOWrapper(in_file, encoding='UTF-8')

 with f as csvfile:
  reader = csv.reader(csvfile, delimiter=delimiter)

  for service, username, password, phone_number, email, sex, date_of_birth, first_name, last_name, *extra in reader:
   if service not in [s.name for s in Service]:
    raise InvalidService(detail=service)
   extra = serialize_extra(extra)
   if isinstance(extra, dict):
    BotAccount.objects.get_or_create(
     service=service,
     username=username,
     defaults=dict(
      chain=form.cleaned_data['chain'],
      extra=extra,
      password=password, phone_number=phone_number,
      email=email, create_type=create_type, sex=sex, date_of_birth=date_of_birth, first_name=first_name,
      last_name=last_name,
     )
    )
    continue

   BotAccount.objects.get_or_create(
    service=service,
    username=username,
    defaults=dict(
     chain=form.cleaned_data['chain'],
     extra=extra,
     password=password, phone_number=phone_number,
     email=email, create_type=create_type, sex=sex, date_of_birth=date_of_birth, first_name=first_name,
     last_name=last_name,
    )
   )


def faker_lmgs_task(lmgs_task_instance: LemmingsTask):
 """Генерирует фейковые данные для аккаунта если их нет"""
 from faker import Faker
 # надо импортить в функции чтобы у потоков были разные faker'ы
 fake = Faker()

 if not lmgs_task_instance.sex:
  lmgs_task_instance.sex = str(randint(0, 1))

 if not lmgs_task_instance.first_name:
  lmgs_task_instance.first_name = (
   fake.first_name_male() if lmgs_task_instance.sex == '0' else fake.first_name_female()
  )
 if not lmgs_task_instance.last_name:
  lmgs_task_instance.last_name = (
   fake.last_name_male() if lmgs_task_instance.sex == '0' else fake.last_name_female()
  )
 if not lmgs_task_instance.birthday:
  lmgs_task_instance.birthday = fake.date_between(end_date='-18y')
 lmgs_task_instance.save()


def _create_periodic_task(
  name: str,
  task_signature: Signature,
  callback_signature: Signature,
  interval: IntervalSchedule = None,
  clocked: ClockedSchedule = None,
  crontab: CrontabSchedule = None,
  solar: SolarSchedule = None,
  priority=CELERY_TASK_DEFAULT_PRIORITY,
):
 serialized_task_signature = dill.dumps(task_signature)
 serialized_callback_signature = dill.dumps(callback_signature)

 serialized_task_signature_sign = sign_task_signature(serialized_task_signature)
 serialized_callback_signature_sign = sign_task_signature(serialized_callback_signature)

 one_off = clocked is not None

 return PeriodicTask.objects.get_or_create(
  name=f'{name}-{sha256(serialized_task_signature + serialized_callback_signature).hexdigest()}',
  task_signature=serialized_task_signature,
  task_signature_sign=serialized_task_signature_sign,
  callback_signature=serialized_callback_signature,
  callback_signature_sign=serialized_callback_signature_sign,
  interval=interval,
  clocked=clocked,
  crontab=crontab,
  solar=solar,
  priority=priority,
  one_off=one_off
 )


def run_celery_task(lmgs_task_instance: LemmingsTask, periodic: bool = False, start_date: datetime.datetime = None):
 faker_lmgs_task(lmgs_task_instance)
 kwargs = lmgs_task_instance.kwargs
 date_of_birth = '' if lmgs_task_instance.birthday is None else str(lmgs_task_instance.birthday)
 kwargs['init_data'] = {
  **kwargs.get('init_data', {}),
  'date_of_birth': date_of_birth,
  'first_name': lmgs_task_instance.first_name,
  'last_name': lmgs_task_instance.last_name
 }

 if lmgs_task_instance.sex and lmgs_task_instance.sex.isdecimal():
  kwargs['init_data']['sex'] = int(lmgs_task_instance.sex)

 save_lmgs_task_sig = save_lemmings_result_task.s(
  django_task_ids=[lmgs_task_instance.id],
  task_identifier=f'save:lmgs:{lmgs_task_instance.action}:{lmgs_task_instance.id}',
  is_internal=True
 )

 task: Signature = run_lemmings_task.s(
  task_identifier=f'lmgs:{lmgs_task_instance.id}',
  action=lmgs_task_instance.action,
  lmgs_kwargs=kwargs,
  queue_name=lmgs_task_instance.chain.task_queue_name
 )

 task_result = task.freeze()

 lmgs_task_instance.task_id = task_result.id
 lmgs_task_instance.save()

 if periodic:
  clocked = ClockedSchedule.objects.create(
   clocked_time=start_date
  )
  _create_periodic_task(
   name=task.name, task_signature=task,
   clocked=clocked,
   callback_signature=save_lmgs_task_sig,
  )
  return

 task.apply_async(link=save_lmgs_task_sig)