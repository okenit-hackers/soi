import logging
from datetime import datetime, timedelta
from random import random

from celery import chain
from celery.app.base import Celery
from django.core import serializers

from lemmings_app.models import BotAccount
from soi_tasks.core import app as external_app
from soi_tasks.internal import app as internal_app
from stereotypes_generator.behavior_emulator.utils import BehaviorServiceController
from stereotypes_generator.settings import COUNT_OF_ACTIONS

logger = logging.getLogger(__name__)


@internal_app.on_after_finalize.connect
def enable_behavior_emulation_periodic_task(sender: Celery, **kwargs):
 sender.add_periodic_task(
  3600, # TODO: Припилить сюда crontab. Не получилось ранее(не запускались таски)
  sig=emulate_behavior.s(
   task_identifier='emulate_behavior_task',
   is_internal=True,
  ),
 )


@internal_app.task(bind=True)
def emulate_behavior(sender: Celery, **kwargs):
 accounts_to_be_emulated = BotAccount.objects.filter(banned=False, enable_behavior_emulation=True, authorized=True)
 logger.info(f'Initiating behavior emulation for {len(accounts_to_be_emulated)} bot accounts')
 for account in accounts_to_be_emulated:
  serialized_account = serializers.serialize('json', account)
  start_signature = start_behavior_emulation.s(
   bot_account_dict=serialized_account,
   task_identifier=f'behavior_emulation:[{account.service.lower()}]{account.username}',
   queue_name=account.lemmings_task.chain.task_queue_name,
  )
  handle_results_signature = handle_emulation_results.s(
   bot_account_id=account.id,
   task_identifier=f'handle_behavior_emulation:[{account.service.lower()}]{account.username}',
   is_internal=True
  )
  tasks_chain = chain(start_signature, handle_results_signature)
  # eta отвечает за отложенный запуск
  tasks_chain.apply_async(eta=datetime.utcnow() + timedelta(seconds=random() * 3600))


@internal_app.task(bind=True)
def handle_emulation_results(
  self,
  last_task_result: dict,
  *args,
  bot_account_id: int,
  task_identifier: str,
  is_internal=False,
  **kwargs
):
 logger.info(f'Handling task [{task_identifier}]')
 if last_task_result.get('cookies') is None:
  logger.info(f'Task [{task_identifier}] did not return any cookies. Behavior emulation completed')
  return
 handling_bot_account = BotAccount.objects.get(id=bot_account_id)
 handling_bot_account.cookies = last_task_result.get('cookies')
 handling_bot_account.last_authorize = datetime.utcnow()
 update_fields = ['cookies', 'last_authorize']
 if last_task_result.get('banned') is not None:
  handling_bot_account.banned = last_task_result.get('banned')
  update_fields.append('banned')
 handling_bot_account.save(update_fields=update_fields)
 logger.info(f'Behavior emulation for task [{task_identifier}] successfully completed')


@external_app.task(bind=True)
def start_behavior_emulation(
  self,
  *,
  bot_account_dict: dict,
  task_identifier: str,
  queue_name: str,
  is_internal=False,
):
 logger.info(f'Initiating behavior emulation for task [{task_identifier}]')
 behavior_emulator_service = BehaviorServiceController.get_behavior_emulator_controller(
  # todo use django serialized object
  bot_account_dict.get['service'].lower()
 )
 behavior_emulator_service = behavior_emulator_service(**bot_account_dict)
 # TODO: Реализовать уникальное кол-во COUNT_OF_ACTIONS для каждого сервиса
 return behavior_emulator_service.emulate_behavior(COUNT_OF_ACTIONS, **bot_account_dict)