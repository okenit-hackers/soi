import logging
import random

from lemmings.botfarm import Controller

from notifications_app.models import Notification
from soi_tasks.core import app as external_app
from soi_tasks.internal import app as internal_app


logger = logging.getLogger(__name__)

MIN_BALANCE_ACCOUNT = 200


@external_app.task(bind=True)
def check_balance(
  self,
  previous_task_result,
  task_identifier: str,
  queue_name: str = None,
  is_internal: bool = False,
):
 from ledger_app.models import PhoneRentAccount

 phone_rent_accounts = previous_task_result['phone_rent_accounts']
 account_state = PhoneRentAccount.AccountState
 for phone_rent_account in phone_rent_accounts:
  logger.info(f'Start to check balance for {phone_rent_account["username"]}')
  try:
   sms_service_class = Controller._get_sms_service_class(phone_rent_account['rent_service_type'])
   sms_service_instance = sms_service_class(phone_rent_account['api_key'], None)
   balance = sms_service_instance.get_balance()
   if balance == 'BAD_KEY':
    phone_rent_account['account_state'] = account_state.bad_key.value
   elif balance == 'ERROR_SQL':
    phone_rent_account['account_state'] = account_state.error_sql.value
   else:
    balance = float(balance)
    phone_rent_account['balance'] = balance
    phone_rent_account['account_state'] = account_state.available.value if balance > 60 else account_state.not_available.value
   # phone_rent_account.save(update_fields=['account_state', 'balance'])
  except Exception as e:
   logger.exception(f'[check_balance] Catch error {e}')
   phone_rent_account['account_state'] = account_state.not_available.value
   # phone_rent_account.save(update_fields=['account_state'])
   continue
 return phone_rent_accounts


@internal_app.task(bind=True)
def check_accounts(
  self,
  phone_rent_accounts,
  task_identifier: str,
  is_internal: bool = True,

):
 """Заглушка чтобы был previous_task_result"""
 return {'phone_rent_accounts': phone_rent_accounts}


@internal_app.task(bind=True)
def change_accounts_state(
  self,
  previous_task_result,
  task_identifier: str,
  is_internal: bool = True,

):
 from ledger_app.models import PhoneRentAccount

 for account in previous_task_result:
  phone_rent_account = PhoneRentAccount.objects.get(pk=account['id'])
  logger.info(f'Start to {change_accounts_state.__name__} for {phone_rent_account.username}')
  phone_rent_account.account_state = account['account_state']
  phone_rent_account.balance = account['balance']
  phone_rent_account.save(update_fields=['balance', 'account_state', ])
  if phone_rent_account.balance < MIN_BALANCE_ACCOUNT:
   Notification.send_to_all(
    f'Баланс {account["rent_service_type"]} - {account["balance"]} р.\nРекомендуем пополнить счет!',
    log_level=Notification.LogLevelChoice.COLOR_DANGER)


@internal_app.task(bind=True)
def periodic_task_check_sms_service_balance(
  self,
  queue_name: str = None,
  task_identifier: str = None
):
 from ledger_app.models import PhoneRentAccount
 from anon_app.models import Chain

 logger.info(f'[{task_identifier}]: started')
 phone_rent_accounts_objects = PhoneRentAccount.objects.all()
 phone_rent_accounts = list(phone_rent_accounts_objects.values())
 for i, account in enumerate(phone_rent_accounts_objects):
  phone_rent_accounts[i]['rent_service_type'] = account.service.name
 chain = random.choice(Chain.objects.filter(status=Chain.StatusChoice.READY))
 PhoneRentAccount.check_balance_chain(chain.task_queue_name, phone_rent_accounts).apply_async()
