import json
import logging
import random
import time
import traceback
from datetime import datetime
from typing import List, Tuple, Union
import requests
import os

import lemmings
import requests.exceptions
from celery_once import QueueOnce
from django.core import serializers
from django.db.models import Q
from django.utils import timezone
from lemmings.botfarm import Controller, Service, ServiceTaskType, Shortcut
from lmgs_botservices.exceptions import ServiceProxyError
from lmgs_botservices.proxy import get_country_code
from lmgs_datasource.phone import Phone
from lmgs_datasource.shortcuts import get_new_phone_number
from lmgs_datasource.sms_enums import country_set, CountryEnum

from anon_app.exceptions import ServiceNotAvailableError
from anon_app.models import Chain, Proxy
from anon_app.tasks.utils import MICROSOCKS_PROTOCOL, MICROSOCKS_IP, MICROSOCKS_PORT
from anon_app.utils import ProxyChanger
from lemmings_app.exceptions import BotAccountProxyError, LemmingsError
from lemmings_app.models import AccountPoolSetting, BehaviorBots, BotAccount, LemmingsTask
from notifications_app.models import Notification
from soi_app.settings import EXPIRE_TIME_FOR_AUTH_TASKS, TIMEOUT_BEFORE_START_AUTH
from soi_tasks.botfarm import app as internal_app
from soi_tasks.core import app as external_app

logger = logging.getLogger(__name__)
MAILS_INDEX = 0

TELETHON_SERVER = os.environ.get('TELETHON_SERVER', '147.45.254.67:8001')

TOKEN = os.environ.get('TOKEN_TELETHON_SERVER', 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJqb2huIn0.6P2F3kA-4p8NdmCbsDmFFQqZx-LqmYNAUCs1otO-nE8')


@external_app.task(bind=True)
def run_lemmings_task(
  self,
  previous_task_result=None,
  action: str = None,
  lmgs_kwargs: dict = None,
  task_identifier: str = None,
  queue_name: str = None,
  is_internal=False
):
 """
 Функция запускает задачу генерации/авторизации ботов.
 Именованные агрументы подаются в вызываемый метод.

 :param previous_task_result: результат предыдущей задачи
 :param action: необходимое действие (функции из lemmings.botfarm.Shortcut)
 :param lmgs_kwargs: параметры задачи
 :param task_identifier: Идентификатор задачи
 :param queue_name: имя используемой очереди
 :param is_internal: если истина, то задача отправится на внутреннюю очередь (вопреки chain.task_queue_name)
 """

 if action is None:
  raise LemmingsError(f'Need action [{task_identifier}]')

 logger.info(
  f'Start {task_identifier} task ' +
  (f'[queue_name={queue_name}]' if queue_name is not None else '') +
  (f'[internal]' if is_internal else '')
 )

 data = {} if previous_task_result is None else previous_task_result
 kwargs = {**lmgs_kwargs} if lmgs_kwargs else {}

 if data:
  if len(data.keys()) > 1:
   raise LemmingsError('Max chain size is 2')

  previous_task_action = list(data.keys())[0]

  if not previous_task_action.startswith('create'):
   raise LemmingsError('You can use chain tasks if first is creating task')

  kwargs.update({
   'username': data[previous_task_action]['username'],
   'phone_number': data[previous_task_action]['phone_number'],
   'password': data[previous_task_action]['password'],
  })

 # todo: добавить сюда валидацию из lemmings_app.views.BotAccountCredentialViewSet.validate
 shortcut = getattr(lemmings.botfarm.Shortcut, action)
 if 'phone_number' in kwargs:
  kwargs.update({'phone': lemmings.datasource.phone.Phone(kwargs['phone_number'])})
 result = shortcut(**kwargs)
 data.update({
  action: result
 })
 return data


# noinspection DuplicatedCode,PyIncorrectDocstring
@internal_app.task(bind=True)
def save_lemmings_result_task(
  self,
  previous_task_result=None,
  django_task_ids: List[int] = None,
  task_identifier: str = None,
  is_internal=True,
  queue_name: str = None,
):
 """
 Функция сохраняет результаты задач генерации/авторизации ботов.

 :param previous_task_result: результат предыдущей задачи
 :param django_task_ids: id записей задач в LemmingsTask
 :param task_identifier: Идентификатор задачи
 :param is_internal: если истина, то задача отправится на внутреннюю очередь (вопреки chain.task_queue_name)
 :param queue_name: имя используемой очереди
 """

 if previous_task_result is None:
  raise LemmingsError(f'No found data [{task_identifier}]')

 if not django_task_ids:
  raise LemmingsError(f'Need django_create_task_id or django_login_task_id [{task_identifier}]')

 for task_id in django_task_ids:
  task = LemmingsTask.objects.get(id=task_id)
  data: dict = previous_task_result.get(task.action, {})
  if not data:
   continue
  task.result = {**task.result, **data}
  task.save(update_fields=['result'])

  if task.action.startswith('create'):
   BotAccount.objects.create(
    service=data.get('service', '').upper(),
    username=data.get('username'),
    password=data.get('password'),
    phone_number=data.get('phone_number'),
    email=data.get('extra_data', {}).get('email', ''),
    lemmings_task=task,
    extra=data.get('extra_data'),
   )
  else:
   bot_account = BotAccount.objects.get(
    username=task.kwargs.get('username'),
    service__iexact=task.kwargs.get('service')
   )
   bot_account.authorized = True
   bot_account.last_authorize = datetime.now()
   bot_account.extra.update(data)
   bot_account.save(update_fields=['extra', 'authorized', 'last_authorize'])

 return previous_task_result


@internal_app.task(bind=True, base=QueueOnce, once={"graceful": True})
def account_quantity_check(
 self,
 is_internal=True,
 queue_name: str = None,
 task_identifier="account_quantity_check",
):
 logger.info(f"{account_quantity_check.__name__} was started")
 required_accounts = AccountPoolSetting.get_missing_accounts()
 required_accounts_count = required_accounts.count()
 bot_behaviors = BehaviorBots.objects.all()
 if required_accounts_count > 0:
  Notification.send_to_all(
   f"Запущена задача по созданию ботов. Резерв аккаунтов для цепочек анонимизации = {required_accounts_count}",
   log_level=Notification.TextColors.COLOR_INFO.value,
  )
 for required in required_accounts:
  now = timezone.now()
  required_timeout = required.sleep_between_runs
  if required.last_triggered_at is None or (now - required.last_triggered_at).seconds > required_timeout:
   time.sleep(1) # сознательное замедление процесса создания новых задач регистрации

   if not already_in_active_task(service=required.service, task_queue_name=required.chain.task_queue_name):
    bot_account_data = {
     'service': required.service,
     'chain': required.chain,
     'create_type': BotAccount.CreateType.GENERATED.value
    }
    if required.behavior_bot and required.is_need_set_behavior:
     bot_account_data['behavior_bot'] = required.behavior_bot
    else:
     if bot_behaviors.exists() and required.is_need_set_behavior:
      bot_account_data['behavior_bot'] = random.choice(bot_behaviors)
    if bot_account_data.get('behavior_bot'):
     bot_account_data['enable_behavior_emulation'] = True
    bot_account = BotAccount.objects.create(**bot_account_data)
    required.last_triggered_at = now
    required.save()
    logger.info(f'Run registration in {bot_account.service} for {bot_account.chain}')
   else:
    logger.info(f"Wait timeout Run registration in {required.service} for {required.chain}")


@internal_app.task(bind=True)
def add_bot_to_pool(
 self,
 service,
 chain_title,
 behavior_bot,
 is_need_set_behavior,
 queue_name: str = None,
 is_internal=True,
 task_identifier: str = None,
):
 chain = Chain.objects.filter(title=chain_title).first()
 bot_behaviors = BehaviorBots.objects.all()
 bot_account_data = {
  "service": service,
  "chain": chain,
  "create_type": BotAccount.CreateType.GENERATED.value,
 }
 if is_need_set_behavior and bot_behaviors.exists():
  bot_account_data["behavior_bot"] = behavior_bot if behavior_bot else random.choice(bot_behaviors)
 if bot_account_data.get("behavior_bot"):
  bot_account_data["enable_behavior_emulation"] = True
 bot_account = BotAccount.objects.create(**bot_account_data)
 logger.info(f"Start created new bot for {bot_account} service!")


@internal_app.task(bind=True, base=None)
def account_pool_check(
 self,
 queue_name: str = None,
 is_internal=True,
 task_identifier: str = None,
):
 accounts_pool_settings = AccountPoolSetting.objects.all()
 if not accounts_pool_settings.exists():
  return

 account_ready = Q(account_state=BotAccount.STATE.READY)
 account_busy = Q(account_state=BotAccount.STATE.ACCOUNT_BUSY)

 for required in accounts_pool_settings:
  time.sleep(1)
  count_alive_account = BotAccount.objects.filter(
   Q(service=required) & (account_ready | account_busy)
  ).count()
  needed_quantity = required.needed_quantity - count_alive_account
  if needed_quantity <= 0:
   continue
  if required.amount_of_attempts_to_create_accounts <= required.attempts_counter:
   Notification.send_to_all(
    f"Количество попыток регистрации для сервиса {required.service} исчерпано",
    log_level=Notification.TextColors.COLOR_WARNING.value,
   )
   logger.info(
    f"Attempts to register a new account have been exhausted({required.service})"
   )
   continue

  logger.info(f"{account_pool_check.__name__} was started")
  Notification.send_to_all(
   f"Запущена задача по созданию аккаунтов для сервиса {required.service}",
   log_level=Notification.TextColors.COLOR_INFO.value,
  )
  now = timezone.now()
  cooldown = required.sleep_between_runs
  if (
   required.last_triggered_at is not None
   and (now - required.last_triggered_at).seconds < cooldown
  ):
   time.sleep(cooldown)
  if not already_in_active_task(
   service=required.service, task_queue_name=required.chain.task_queue_name
  ):
   last_bot = BotAccount.objects.filter(service=required.service).first()
   if last_bot is not None and last_bot.STATE not in ("READY", "ACCOUNT_BUSY"):
    required.attempts_counter += 1
   else:
    required.attempts_counter = 0
   logger.info(f"Run registration in {required.service} for {required.chain}")
   required.last_triggered_at = now
   required.save()
   add_bot_to_pool.apply_async(
    kwargs={
     "service": required.service,
     "chain_title": required.chain.title,
     "behavior_bot": required.behavior_bot,
     "is_need_set_behavior": required.is_need_set_behavior,
     "queue_name": "internal_celery",
     "task_identifier": "add_account",
    },
   )
  else:
   logger.info(
    f"Registration in {required.service} for {required.chain} skipped. "
    f"The same active task was found."
   )


@internal_app.task(bind=True, base=QueueOnce, once={'graceful': True})
def account_auth_check(
  self, behavior_bots_pk, is_internal=True, queue_name: str = None, task_identifier='account_auth_check'
):
 logger.info(f'{account_auth_check.__name__} was started')
 bot_accounts = BotAccount.objects.filter(behavior_bot=behavior_bots_pk).filter(
  Q(account_state=BotAccount.STATE.READY) | Q(account_state=BotAccount.STATE.ERROR_SERVICE_CHECK)
 )
 for bot_account in bot_accounts:
  time_to_sleep = random.randrange(0, TIMEOUT_BEFORE_START_AUTH)
  now = timezone.localtime(timezone.now())
  launch_time = now + timezone.timedelta(seconds=time_to_sleep)
  expire_time = now + timezone.timedelta(seconds=EXPIRE_TIME_FOR_AUTH_TASKS)
  bot_account.make_login_chain().apply_async(eta=launch_time, expires=expire_time)
  logger.info(f'Sleep {time_to_sleep} sec. before start checking {bot_account}.')


@internal_app.task(bind=True, base=QueueOnce, once={'graceful': True})
def accounts_busy_checker(
  self, is_internal=True, queue_name: str = None, task_identifier='accounts_busy_checker'
):
 logger.info(f'{accounts_busy_checker.__name__} was started')
 time_now = timezone.now()
 thirty_minutes_ago = time_now - timezone.timedelta(minutes=30)
 start_date = time_now - timezone.timedelta(weeks=1000)
 busy_bot_accounts = BotAccount.objects.filter(
  Q(account_state=BotAccount.STATE.ACCOUNT_BUSY) & Q(changed__range=(start_date, thirty_minutes_ago))
 )
 logger.info(f'start to change {len(busy_bot_accounts)=} state to ready')
 busy_bot_accounts.update(account_state=BotAccount.STATE.READY)


@internal_app.task(bind=True, base=QueueOnce, once={'graceful': True})
def check_chains_proxy_limit(
  self, is_internal=True, queue_name: str = None, task_identifier='check_chains_proxy_limit'
):
 chains = Chain.objects.all().filter(check_proxy_limit=True)
 for chain in chains:
  if chain.get_alive_proxies_query_with_conditions().count() <= chain.proxy_limit:
   Notification.send_to_all(
    content=f'Цепочка {chain.title} достигла лимита прокси',
    log_level=Notification.LogLevelChoice.COLOR_WARNING.value
   )
   chain.check_proxy_limit = False
   chain.save(update_fields=['check_proxy_limit', ])


def already_in_active_task(service: str, task_queue_name: str):
 # получаем инспектора celery и достаём активные таски у всех воркеров.
 inspector = internal_app.control.inspect()
 active_tasks = inspector.active()

 # Примерно так выглядят все активные таски.
 # Это словарь, ключами которого являются имена воркеров (не путать с названием очередей),
 # а значениями являются списки тасков.
 # active_tasks = {
 #  'celery@debian-dev-01': [
 #   {
 #    'id': 'a73819a9-53d0-46c3-a239-947c76d84c47',
 #    'name': 'lemmings_app.tasks.stub_external_task',
 #    'args': [None],
 #    'kwargs': {
 #     'queue_name': 'debug',
 #     'service': 'VK',
 #     'task_identifier': 'stub_internal_task'
 #    },
 #    'type': 'lemmings_app.tasks.stub_external_task',
 #    'hostname': 'celery@debian-dev-01',
 #    'time_start': 1629369134.9635787,
 #    'acknowledged': False,
 #    'delivery_info': {'exchange': '', 'routing_key': 'debug', 'priority': 0, 'redelivered': None},
 #    'worker_pid': 215056
 #   }
 #  ]
 # }
 for worker_name in active_tasks.keys():
  worker_tasks_list = active_tasks[worker_name]

  # для каждого воркера проверяем аргументы для активных задач
  for worker_task in worker_tasks_list:
   task_kwargs = worker_task['kwargs']

   if 'bot_pk' in task_kwargs.keys():
    # наличие 'bot_pk' в kwargs задачи позволяет найти объект, а по нему определить сервис и очередь
    # цепочки, в которой производится работа с аккаунтом
    ba = BotAccount.objects.get(pk=task_kwargs['bot_pk'])
    if ba.chain.task_queue_name == task_queue_name and ba.service == service:
     # Если среди активных задач нашлась такая, у которой в аргументах есть bot_pk, сервис и очередь
     # которого совпадает с искомым, то возвращаем True - есть активная задача регистрации для этого
     # сервиса на этой цепочке
     logger.info(f'Found registration task for {service} in internal worker')
     return True
    else:
     continue
   elif 'queue_name' in task_kwargs.keys() and 'service' in task_kwargs.keys():
    # Если среди активных задач нашлась такая, у которой в аргументах есть такая, у которой в аргументах
    # есть искомые сервис и название очереди, то возвращаем True - есть активная задача регистрации для
    # этого сервиса на этой цепочке.
    if task_queue_name == task_kwargs['queue_name'] and service == task_kwargs['service']:
     logger.info(f'Found registration task for {service} in remote worker')
     return True
    else:
     continue
   else:
    # работает другая задача, например, задача сбора.
    pass
 # активных задач регистрации в сервисе service на цепочке с очередью task_queue_name не нашлось.
 return False


def proxy_to_string(proxy: dict) -> str:
 if proxy['username'] and proxy['password']:
  return f"{proxy['protocol'].lower()}://{proxy['username']}:{proxy['password']}@{proxy['ip']}:{proxy['port']}"
 else:
  return f"{proxy['protocol'].lower()}://{proxy['ip']}:{proxy['port']}"


def change_proxy_state_during_the_task(proxy: dict):
 """Меняет статус прокси на Blacklist или на USED"""
 proxy_pk = proxy['pk']
 proxy = Proxy.objects.get(pk=proxy_pk)
 if proxy.number_of_applying == Proxy.NumberOfApplyingChoice.DISPOSABLE.value:
  proxy.applying = proxy.ApplyingChoice.BLACKLIST.value
  logger.info(f'Proxy - {proxy} state was changed on BLACKLIST')
 elif proxy.number_of_applying == Proxy.NumberOfApplyingChoice.REUSABLE.value and \
   proxy.applying != Proxy.ApplyingChoice.USED.value:
  proxy.applying = proxy.ApplyingChoice.USED.value
  logger.info(f'Proxy - {proxy} state was changed on USED')
 proxy.save(update_fields=['applying'])


@internal_app.task(bind=True)
def prepare_proxy(self,
  *args,
  bot_pk: int,
  is_internal=True,
  queue_name: str = None,
  task_identifier: str = None
):
 logger.info(f'Start {prepare_proxy.__name__} {bot_pk}')

 for i in range(1000):
  try:
   bot = BotAccount.objects.get(pk=bot_pk)
   break
  except BotAccount.DoesNotExist:
   # waiting instance save in db
   time.sleep(1)
   continue
 else:
  raise BotAccount.DoesNotExist(f'I can\'t found the object with pk {bot_pk}')
 anon_chain = bot.chain
 service_name = bot.service

 raw_proxies = args[0] if args else None
 if raw_proxies is not None:
  not_banned_proxies = [
   p for p in raw_proxies
   if service_name not in p['fields']['services'].keys()
   or not p['fields']['services'][service_name]['banned']
  ]
  proxies = [
   {
    'pk': p['pk'],
    'url': proxy_to_string(p['fields'])
   }
   for p in not_banned_proxies
  ]
  current_proxy = random.choice(proxies)
 else:
  proxies = []
  current_proxy = None

 if anon_chain.has_proxies_chain:
  current_proxy = f'{MICROSOCKS_PROTOCOL}://{MICROSOCKS_IP}:{MICROSOCKS_PORT}'
 elif current_proxy:
  change_proxy_state_during_the_task(current_proxy)

 return {
  'proxies': proxies,
  'current_proxy': current_proxy,
  'used_proxies': []
 }


@internal_app.task(bind=True)
def bio_generate(self,
  proxy: dict,
  bot_pk: int,
  queue_name: str = None,
  task_identifier: str = None,
  is_internal=True,
):
 logger.info(f'Start {bio_generate.__name__}')
 task_result = {
  'proxy': proxy,
  'last_action': bio_generate.__name__,
  'error': None
 }
 ba = BotAccount.objects.get(pk=bot_pk)
 try:
  user_bio = {
   'first_name': ba.first_name,
   'last_name': ba.last_name,
   'date_of_birth': ba.date_of_birth,
   'sex': ba.sex,
  }
  need_to_update = user_bio.keys()

  bio_info = BotAccount.generate_bio(user_bio, task_result)
  bio_info['image_bs64'] = bio_info['image_bs64'].decode()

  ba.first_name = bio_info['first_name']
  ba.last_name = bio_info['last_name']
  ba.date_of_birth = bio_info['date_of_birth']
  ba.sex = bio_info['sex']
  logger.info(f'got {bio_info["sex"]}')
  ba.save(update_fields=need_to_update)

  bio_info['sex'] = int(bio_info['sex'])

  task_result[bio_generate.__name__] = bio_info
 except Exception as e:
  logger.error(e, exc_info=True)
  task_result['error'] = e.__class__.__name__
  task_result['traceback'] = traceback.format_exc()

 return task_result


@internal_app.task(bind=True)
def save_bot_info(self,
  previous_task_result,
  bot_pk: int,
  queue_name: str = None,
  task_identifier: str = None,
  is_internal=True,
):
 logger.info(f'Start {save_bot_info.__name__} {bot_pk}')
 account = BotAccount.objects.get(pk=bot_pk)
 account.extra = dict(account.extra, **previous_task_result)

 try:
  if previous_task_result['last_action'] == bio_generate.__name__:
   account.do_bio_generate(task_result=previous_task_result)
  elif previous_task_result['last_action'] == reg_required_account.__name__:
   account.do_reg_requirements_account(task_result=previous_task_result)
  elif previous_task_result['last_action'] == get_phone.__name__:
   account.do_get_phone(task_result=previous_task_result)
  elif previous_task_result['last_action'] == reg_in_service.__name__:
   account.do_reg_in_service(task_result=previous_task_result)
  elif previous_task_result['last_action'] == save_account.__name__:
   account.do_save_account(task_result=previous_task_result)
  elif previous_task_result['last_action'] == import_account.__name__:
   account.do_import(task_result=previous_task_result)
  elif previous_task_result['last_action'] == login_account.__name__:
   account.do_login_service(task_result=previous_task_result)
   account.save()
   account.do_save_auth(task_result=previous_task_result)
   account.save()
   account.to_success(task_result=previous_task_result)
   account.extra[bio_generate.__name__].pop('image_bs64', None) # remove image from saved data
   account.extra[reg_in_service.__name__].get('extra_data', {}).pop('image_bs64', None) # for fb
   account.save(update_fields=['extra'])
   logger.info(f'End {save_bot_info.__name__}')
 finally:
  account.save()
 return {
  save_bot_info.__name__: 'ok',
  'last_action': save_bot_info.__name__,
  'extra': account.extra
 }


@internal_app.task(bind=True, time_limit=20 * 60, soft_time_limit=19 * 60)
def reg_required_account(
  self,
  previous_task_result,
  service: str,
  needs_new_mail: bool,
  dependency_services: list,
  chain_pk: int,
  is_internal=True,
  queue_name: str = None,
  task_identifier: str = None
):
 logger.info(f'Start {reg_required_account.__name__}')
 task_result = {
  'last_action': reg_required_account.__name__,
  'error': None,
 }

 free_accounts = []
 if not needs_new_mail and dependency_services:
  used_accounts_ids = list(BotAccount.objects.filter(service=service).values_list(
   'required_account', flat=True))
  free_accounts = list(BotAccount.objects.filter(
   service__in=dependency_services[MAILS_INDEX], account_state='READY',
  ).exclude(id__in=used_accounts_ids))
 if not free_accounts:
  for dependence in dependency_services:
   free_accounts.append(reg_dependence_account(chain_pk, dependence, task_result))
 try:
  serialized_dependencies = serializers.serialize('json', free_accounts)
  task_result[reg_required_account.__name__] = json.loads(serialized_dependencies)
 except AttributeError:
  if task_result.get('error') is None:
   logger.error(f'Critical error locals = {locals()}')
   raise

 return task_result


@external_app.task(bind=True, time_limit=20 * 60, soft_time_limit=10 * 60)
def get_phone(
  self,
  previous_task_result,
  queue_name: str,
  service: str,
  task_identifier: str,
  is_internal: bool = False
):
 logger.info(f'Start {get_phone.__name__}')
 task_result = {
  'last_action': get_phone.__name__,
  'error': None
 }

 if Service.__getattr__(service) in (Service.REDDIT, Service.MYSPACE):
  task_result['proxy'] = previous_task_result['extra']['proxy'],
  task_result[get_phone.__name__] = None
  return task_result
 try:
  current_proxy = previous_task_result['extra']['proxy'].get('current_proxy')
  country, default_country, available_operators = get_country(
   service,
   proxy=current_proxy['url'] if current_proxy is not None else current_proxy,
  )
  logger.info(f'Try reserve phone in {country.name}')
  phone_number, phone_data = get_new_phone_number(
   Service.__getattr__(service),
   extra_info={'country': country},
   default_country=default_country
  )
  phone = Phone(phone_number)

  phone_info = {
   'phone': phone.number,
   'country': country.name,
   'phone_data': phone_data,
  }

  task_result[get_phone.__name__] = phone_info
  logger.info(f'Successfully {get_phone.__name__}')
 except Exception as E:
  logger.error(E, exc_info=True)
  task_result['error'] = str(E)
  task_result['traceback'] = traceback.format_exc()

 return task_result


@external_app.task(bind=True, time_limit=20 * 60, soft_time_limit=10 * 60)
def reg_in_service(
  self,
  previous_task_result,
  queue_name: str,
  service: str,
  task_identifier: str,
  is_internal: bool = False
):
 logger.info(f'Start {reg_in_service.__name__}')

 task_result = {
  'last_action': reg_in_service.__name__,
  'error': None
 }
 try:
  proxy = previous_task_result['extra']['proxy']
  current_proxy = proxy['current_proxy']
  proxies = proxy['proxies']
  used_proxies = proxy['used_proxies']
  _proxy = current_proxy.get('url') if current_proxy is not None else None

  instance = Controller()
  reg_method = instance.create_bot
  country = country_set.get(get_country_code(_proxy), CountryEnum.NETHERLANDS).name

  first_name = previous_task_result['extra'][bio_generate.__name__]['first_name']
  last_name = previous_task_result['extra'][bio_generate.__name__]['last_name']
  sex = previous_task_result['extra'][bio_generate.__name__]['sex']
  date_of_birth = previous_task_result['extra'][bio_generate.__name__]['date_of_birth']
  image_bs64 = previous_task_result['extra'][bio_generate.__name__]['image_bs64']

  from lemmings_app.utils import create_random_password
  password = create_random_password()

  required_account_info = previous_task_result['extra'][reg_required_account.__name__]

  email_info = dict()
  for i in required_account_info:
   try:
    email_info = {
     'username': i['fields']['extra']['reg_in_service']['username'],
     'imap_password': i['fields']['extra']['reg_in_service']['imap_password'],
     'credentials': i['fields']['extra']['reg_in_service'].get('credentials'),
     'service': i['fields']['extra']['reg_in_service']['service'],
    }
   except KeyError:
    logger.warning('Can\'t get email', exc_info=True)

  init_data = {
   'proxy': _proxy,
   'country': country,
   "first_name": first_name,
   "last_name": last_name,
   'password': password,
   "sex": sex,
   "date_of_birth": date_of_birth,
   'image_bs64': image_bs64,
   'email_info': email_info,
  }

  if previous_task_result['extra'][get_phone.__name__] is not None:
   init_data["phone"] = Phone(previous_task_result['extra'][get_phone.__name__]['phone'])
   init_data["phone_data"] = previous_task_result['extra'][get_phone.__name__]['phone_data']

  if service == 'MAIL_RU':
   # TODO: удалить страшный костыль и передавать нормально
   init_data['need_imap'] = True

  reg_result = reg_method(service=service, init_data=init_data, need_generate=False)

  task_result[reg_in_service.__name__] = reg_result
  logger.info(f'Successfully reg in service {reg_in_service.__name__}')
 except requests.exceptions.SSLError: # tuple of exceptions move in exceptions
  task_result['error'] = BotAccountProxyError.__class__.__name__ # move in exceptions
  task_result['traceback'] = traceback.format_exc()
 except Exception as e:
  if isinstance(e, ServiceProxyError):
   changer = ProxyChanger(proxies, current_proxy, service)
   try:
    proxies, new_proxy, used_proxy = changer.change_proxy()
    used_proxies.extend(used_proxy)
   except ServiceNotAvailableError as e:
    logger.info('There is no any proxies available for this task')
    logger.error(e, exc_info=True)
    task_result['error'] = e.__class__.__name__
    task_result['traceback'] = traceback.format_exc()
   else:
    proxy['current_proxy'] = new_proxy
    proxy['proxies'] = proxies

    logger.info(f'Retrying task for {service} service')
    self.retry(
     args=(
      previous_task_result,
     ),
     kwargs={
      'task_identifier': task_identifier,
      'queue_name': queue_name,
      'service': service
     },
     countdown=10.0,
     max_retries=10
    )
  else:
   logger.error(e, exc_info=True)
   task_result['error'] = e.__class__.__name__
   task_result['traceback'] = traceback.format_exc()

 return task_result


@internal_app.task(bind=True)
def save_account(
  self,
  previous_task_result,
  bot_pk: int,
  is_internal=True,
  queue_name: str = None,
  task_identifier: str = None
):
 logger.info(f'Start {save_account.__name__}')
 task_result = {
  'last_action': save_account.__name__,
  'error': None
 }

 account = BotAccount.objects.get(pk=bot_pk)
 if account.create_type == BotAccount.CreateType.IMPORTED:
  return task_result
 account.username = account.extra[reg_in_service.__name__]['username']
 account.password = account.extra[reg_in_service.__name__]['password']
 account.phone_number = account.extra[reg_in_service.__name__].get('phone_number', None)
 if account.extra["proxy"]["current_proxy"]:
  account.location = Proxy.objects.get(pk=account.extra["proxy"]["current_proxy"]["pk"]).location

 required_account_info = account.extra.get(reg_required_account.__name__)
 if required_account_info:
  required_account_id = required_account_info[0].get('pk')
  account.required_account = BotAccount.objects.get(pk=required_account_id)

 used_proxies = previous_task_result['extra']['proxy'].get('used_proxies', [])
 ProxyChanger.save_proxy_data(used_proxies, account.service)

 account.save(update_fields=['username', 'password', 'phone_number', 'location', 'required_account'])

 return task_result


@internal_app.task(bind=True)
def save_login_bot_info(
  self,
  previous_task_result,
  bot_pk: int,
  queue_name: str = None,
  task_identifier: str = None,
  is_internal=True,
):
 logger.info(f'Start {save_login_bot_info.__name__}')
 account = BotAccount.objects.get(pk=bot_pk)
 account.extra = dict(account.extra, **previous_task_result)

 try:
  if previous_task_result['last_action'] == check_ba.__name__:
   account.do_start_login(task_result=previous_task_result)
  elif previous_task_result['last_action'] == login_account.__name__:
   logger.info(f'[{save_login_bot_info.__name__}] start to check banned error')
   account.do_check_login_banned(task_result=previous_task_result)
   account.save()
   logger.info(f'[{save_login_bot_info.__name__}] start to check errors')
   account.do_check_login(task_result=previous_task_result)
   account.save()
   logger.info(f'End {save_login_bot_info.__name__}')
 finally:
  account.save()

 return {
  save_bot_info.__name__: 'ok',
  'last_action': save_bot_info.__name__,
  'extra': account.extra
 }


@internal_app.task(bind=True)
def save_state_account(
  self,
  *args,
  **kwargs,
):
 logger.info(f'Start {save_state_account.__name__}')
 account = BotAccount.objects.get(pk=kwargs.get('bot_pk'))
 previous_task_result = args[0] if args else None
 if previous_task_result:
  state = previous_task_result.get('state')
  if state == BotAccount.STATE.ACCOUNT_DELIVERED_TO_TAG_ERROR:
   account.go_to_error_delivered_state()
  else:
   account.go_to_delivered_state()
 else:
  account.go_to_busy_state()
 account.save()


def reg_dependence_account(chain_pk, inner_dependencies, task_result):
 last_error, last_traceback = None, None
 for dependence in inner_dependencies:
  try:
   chain = Chain.objects.get(pk=chain_pk)
   required_bot = BotAccount.objects.create(service=dependence, chain=chain, dependency=True)
   workflow = required_bot.make_chain()
   logger.info(f'workflow for {required_bot} created')
   async_result = workflow.delay().get(disable_sync_subtasks=False, timeout=None, interval=1)
   logger.info(f'workflow finished for {required_bot} with status {required_bot.account_state}')

   required_bot = BotAccount.objects.get(pk=required_bot.pk) # update info
   # required_bot.refresh_from_db() # raised AttributeError: Direct account_state modification is not allowed
   return required_bot
  except Exception as e:
   logger.exception(f'Catch error with service {dependence}')
   last_error = e.__class__.__name__
   last_traceback = traceback.format_exc()
 if last_error and last_traceback:
  logger.warning(f'cant reg inner_dependence {last_error=}')
  task_result['error'] = last_error
  task_result['traceback'] = last_traceback


def get_country(service, proxy=None) -> Tuple[CountryEnum, Union[CountryEnum, None], str]:
 """Возвращает страну, страну по умолчанию, операторов, если необходимо"""
 instance = Controller()
 datagenerator_class = instance._get_service_datagenerator_class(service)
 datagenerator_class = datagenerator_class()

 default_country = datagenerator_class.DEFAULT_COUNTRY
 ld_default_country = datagenerator_class.LD_DEFAULT_COUNTRY
 hardcode_country = datagenerator_class.HARDCODE_COUNTRY
 available_operators = datagenerator_class.AVAILABLE_OPERATORS
 exclude_countries = datagenerator_class.EXCLUDE_COUNTRIES

 if isinstance(hardcode_country, CountryEnum):
  return hardcode_country, default_country, ''

 country = country_set.get(get_country_code(proxy), ld_default_country)
 country = country if country not in exclude_countries else ld_default_country
 available_operators = available_operators if country == CountryEnum.RUSSIA and available_operators else ''
 return country, default_country, available_operators


@internal_app.task(bind=True)
def import_account(
  self,
  proxy: dict,
  bot_pk: int,
  is_internal=True,
  queue_name: str = None,
  task_identifier: str = None
):
 logger.info(f'Start {import_account.__name__} {bot_pk}')
 task_result = {
  'proxy': proxy,
  'last_action': import_account.__name__,
  'error': None
 }

 for i in range(1000):
  try:
   account = BotAccount.objects.get(pk=bot_pk)
   break
  except BotAccount.DoesNotExist:
   logger.warning(f'Cannot get {BotAccount.__name__} by {bot_pk=} in attempt {i}')
   time.sleep(1)
   continue
 else:
  raise BotAccount.DoesNotExist(f'Cannot get {BotAccount.__name__} by {bot_pk=}')

 if account.extra is None:
  account.extra = {}

 credentials = {
  'username': account.username,
  'password': account.password,
  'phone_number': str(account.phone_number)
 }
 account.extra[reg_in_service.__name__] = account.extra.get(reg_in_service.__name__, {}) | credentials

 account.save(update_fields=['extra'])

 return task_result


@internal_app.task(bind=True)
def check_ba(self,
  proxy: dict,
  bot_pk: int,
  task_identifier: str,
  is_internal: bool
):
 logger.info(check_ba.__name__)
 task_result = {
  'proxy': proxy,
  'last_action': check_ba.__name__,
  'error': None
 }
 return task_result


@external_app.task(bind=True, time_limit=20 * 60, soft_time_limit=10 * 60)
def login_account(
  self,
  previous_task_result,
  queue_name: str,
  service: str,
  task_identifier: str,
  is_internal: bool = False
):
 logger.info(login_account.__name__)
 task_result = {
  'last_action': login_account.__name__,
  'error': None
 }
 try:
  username = previous_task_result['extra'][reg_in_service.__name__]['username']
  password = previous_task_result['extra'][reg_in_service.__name__]['password']
  phone_number = previous_task_result['extra'][reg_in_service.__name__].get('phone_number', None)
  extra_data = previous_task_result['extra'][reg_in_service.__name__].get('extra_data', {})
  if two_fa := previous_task_result['extra'].get('2fa', None):
   extra_data['2fa'] = two_fa
  current_proxy = previous_task_result['extra']['proxy'].get('current_proxy')
  extra_data['proxy'] = current_proxy['url'] if current_proxy is not None else None
  imap_password = previous_task_result['extra'][reg_in_service.__name__].get('imap_password')
  credentials = previous_task_result['extra'][reg_in_service.__name__].get('credentials')
  if imap_password:
   extra_data['imap_password'] = imap_password
  if credentials:
   extra_data['credentials'] = credentials
  phone = phone_number and Phone(phone_number) # phone = correct Phone or None

  _, login_method, *_ = Shortcut.find_methods(service_name=service, action_type_name=ServiceTaskType.LOGIN)[0]
  logger.info(f'try to login in {service} with username {username}')
  login_result = login_method(username, password, phone, extra_data)
  task_result[login_account.__name__] = login_result
 except Exception as e:
  logger.error(e, exc_info=True)
  task_result['error'] = e.__class__.__name__
  task_result['traceback'] = traceback.format_exc()

 return task_result


@external_app.task(bind=True, time_limit=20 * 60, soft_time_limit=10 * 60)
def push_to_tag(
  self,
  *args,
  **kwargs,
):
 logger.info(push_to_tag.__name__)
 task_result = {
  'last_action': push_to_tag.__name__,
  'error': None
 }
 url = f'http://{TELETHON_SERVER}/bot/add_bot'
 data = {
  'string_session': kwargs.get('string_session'),
  'phone_number': kwargs.get('phone_number'),
 }
 headers = {'Authorization': f'Bearer {TOKEN}'}
 try:
  response = requests.post(url, data=json.dumps(data), headers=headers)
  if response.status_code != 201:
   logger.warning(f'Ответ от {url} не равен 201')
   raise requests.exceptions.RequestException(f'{response.text}')
  task_result['state'] = BotAccount.STATE.ACCOUNT_DELIVERED_TO_TAG
 except Exception as e:
  logger.error(e, exc_info=True)
  task_result['error'] = e.__class__.__name__
  task_result['traceback'] = traceback.format_exc()
  task_result['state'] = BotAccount.STATE.ACCOUNT_DELIVERED_TO_TAG_ERROR
 return task_result


@internal_app.task(bind=True)
def periodic_task_check_bad_bot_accounts(
  self,
  bot_pk: int,
  is_internal=True,
  queue_name: str = None,
  task_identifier: str = None
):
 bot_accounts = BotAccount.objects.all()
 services = list(set([bot_account.service for bot_account in bot_accounts]))
 bad_accounts_query = (
  Q(account_state=BotAccount.STATE.BLOCKED_ACC) |
  Q(account_state=BotAccount.STATE.ERROR_BIO) |
  Q(account_state=BotAccount.STATE.ERROR_SUB_SERVICE) |
  Q(account_state=BotAccount.STATE.ERROR_PHONE) |
  Q(account_state=BotAccount.STATE.ERROR_SERVICE_CHECK) |
  Q(account_state=BotAccount.STATE.ERROR_DB)
 )
 for service in services:
  all_bots = bot_accounts.filter(service=service)
  all_bots_count = all_bots.count()
  problem_bots_count = all_bots.filter(bad_accounts_query).count()
  if all_bots_count == problem_bots_count:
   service_name = all_bots.first().get_service_display()
   Notification.send_to_all(
    content=f'Все аккаунты {service_name} недоступны',
    log_level=Notification.LogLevelChoice.COLOR_DANGER.value,
   )

## в django console
# from celery import chain
# from lemmings_app.tasks import stub_internal_task, stub_external_task
# import time
# chain_task = chain(stub_internal_task.s(bot_pk=1, task_identifier=stub_internal_task.__name__, is_internal=True), stub_external_task.s(queue_name='debug', service='VK', task_identifier=stub_internal_task.__name__))
#
# for i in range(1, 1000):
#  chain_task = chain(stub_internal_task.s(bot_pk=1, task_identifier=stub_internal_task.__name__, is_internal=True),
#  stub_external_task.s(queue_name='debug', service='VK', task_identifier=stub_internal_task.__name__))
#  chain_task.apply_async()
#  time.sleep(21)
#
# @internal_app.task(bind=True)
# def stub_internal_task(
#   self,
#   bot_pk: int,
#   is_internal=True,
#   queue_name: str = None,
#   task_identifier: str = None
# ):
#  logger.info(f'{stub_internal_task.__name__} start {bot_pk=}')
#  time.sleep(10)
#  logger.info(f'{stub_internal_task.__name__} stop {bot_pk=}')
#
#
# @external_app.task(bind=True, time_limit=20 * 60, soft_time_limit=10 * 60)
# def stub_external_task(
#   self,
#   previous_task_result,
#   queue_name: str,
#   service: str,
#   task_identifier: str,
#   is_internal: bool = False
# ):
#  logger.info(f'{stub_external_task.__name__} start {service=}')
#  time.sleep(10)
#  logger.info(f'{stub_external_task.__name__} stop {service=}')