from django.contrib import admin
from django.db import models
from django.utils.translation import gettext_lazy
from celery.canvas import Signature, chain
from django.core.validators import MaxValueValidator

from ledger_app.tasks import check_accounts, check_balance, change_accounts_state
from soi_app import settings


class Account(models.Model):
 class Meta:
  abstract = True

 username = models.CharField(max_length=32, verbose_name=gettext_lazy('username'))
 password = models.CharField(max_length=128, verbose_name=gettext_lazy('password'))


class PaidService(models.Model):
 class Meta:
  ordering = ['-id']
  verbose_name = gettext_lazy('Paid Service')
  verbose_name_plural = gettext_lazy('Paid Service')

 name = models.CharField(max_length=128, verbose_name='Название сервиса')
 url = models.URLField(verbose_name='Ссылка на сервис')
 note = models.TextField(verbose_name='Заметка', blank=True)

 def __str__(self):
  return f"{self.name}"


class Currency(models.Model):
 class Meta:
  ordering = ['-id']
  verbose_name = gettext_lazy('Currency')
  verbose_name_plural = gettext_lazy('Currencies')

 name = models.CharField(verbose_name='Название валюты', max_length=128)
 show_numbers = models.PositiveIntegerField(verbose_name='Количество чисел после запятой',
             validators=[MaxValueValidator(settings.MAX_NUMBER_AFTER_POINT), ],
             null=True)

 def __str__(self):
  return f"{self.name}"


class AbstractServiceAccount(Account):
 class Meta:
  verbose_name = gettext_lazy('Service Account')
  verbose_name_plural = gettext_lazy('Service Account')
  abstract = True

 @property
 @admin.display(
  ordering='service__url',
  description=gettext_lazy('service url'),

 )
 def service_url(self):
  return self.service.url

 service = models.ForeignKey(PaidService, on_delete=models.CASCADE, verbose_name='Сервис')

 def __str__(self):
  return f"{self.service.name} {self.username}"


class ServiceAccount(AbstractServiceAccount):
 class Meta:
  verbose_name = gettext_lazy('Service Account')
  verbose_name_plural = gettext_lazy('Service Account')


class Ledger(models.Model):
 class Meta:
  ordering = ['-id']
  verbose_name = gettext_lazy('Ledger')
  verbose_name_plural = gettext_lazy('Ledger')

 service = models.ForeignKey(PaidService, on_delete=models.CASCADE, verbose_name='Сервис')
 currency = models.ForeignKey(Currency, on_delete=models.CASCADE, verbose_name='Валюта')
 balance = models.DecimalField(max_digits=57, decimal_places=settings.MAX_NUMBER_AFTER_POINT, verbose_name='Баланс')
 account = models.ForeignKey(ServiceAccount, on_delete=models.CASCADE, verbose_name='Аккаунт сервиса')

 def __str__(self):
  return f"{self.service.name}"


class PhoneRent(PaidService):
 class Meta:
  ordering = ['-id']
  verbose_name = gettext_lazy('Rent service')
  verbose_name_plural = gettext_lazy('Rent services')

 class SMSService(models.TextChoices):
  SMS_ACTIVATE = 'SMS_ACTIVATE', gettext_lazy('SMS_ACTIVATE')
  SMS_MAN = 'SMS_MAN', gettext_lazy('SMS_MAN')
  ONLINE_SIM = 'ONLINE_SIM', gettext_lazy('ONLINE_SIM')
  SMS_365 = 'SMS_365', gettext_lazy('SMS_365')

 rent_service_type = models.CharField(
  max_length=20,
  choices=SMSService.choices,
  default=SMSService.SMS_ACTIVATE,
  verbose_name=gettext_lazy('Rent service type')
 )


class PhoneRentAccount(AbstractServiceAccount):
 class Meta:
  ordering = ['-id']
  verbose_name = gettext_lazy('PhoneRentAccount')
  verbose_name_plural = gettext_lazy('PhoneRentAccounts')

 class AccountState(models.TextChoices):
  available = 'available', gettext_lazy('available')
  not_available = 'not_available', gettext_lazy('not available')
  bad_key = 'bad_key', gettext_lazy('bad key')
  error_sql = 'error_sql', gettext_lazy('error sql')

 service = models.ForeignKey(
  PhoneRent, on_delete=models.CASCADE, verbose_name=gettext_lazy('Rent service')
 )
 api_key = models.CharField(max_length=128)
 balance = models.DecimalField(max_digits=30, default=0.00, decimal_places=2, verbose_name=gettext_lazy('Balance'))
 account_state = models.CharField(
  default=AccountState.available.value, max_length=128,
  choices=AccountState.choices, verbose_name=gettext_lazy('Account state')
 )

 @staticmethod
 def check_balance_chain(task_queue_name, phone_rent_accounts) -> Signature:
  from anon_app.models import Chain

  """Create chain of celery"""

  tasks_chain = chain(
   check_accounts.s(
    phone_rent_accounts=phone_rent_accounts,
    task_identifier=check_accounts.__name__, is_internal=True),
   check_balance.s(queue_name=task_queue_name, is_internal=False,
       task_identifier=check_balance.__name__),
   change_accounts_state.s(
    task_identifier=check_accounts.__name__, is_internal=True),
  )

  return tasks_chain