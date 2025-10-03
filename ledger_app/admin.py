import logging
import random
from django.contrib import messages

from anon_app.models import Chain
from ledger_app.models import *

logger = logging.getLogger(__name__)


@admin.register(PaidService)
class PaidServiceAdmin(admin.ModelAdmin):
 fields = ['name', 'url', 'note']
 list_display = ['name', 'url']


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
 fields = ['name', 'show_numbers']


@admin.register(ServiceAccount)
class ServiceAccountAdmin(admin.ModelAdmin):
 fields = ['service', 'username', 'password', ]
 list_display = ['service', 'service_url', 'username', ]


@admin.register(Ledger)
class LedgerAdmin(admin.ModelAdmin):
 fields = ['service', 'account', 'currency', 'balance', ]
 list_display = ['service', 'account', 'currency', 'cut_balance']

 def cut_balance(self, ledger_obj) -> str:
  """
  Функция обрезает до X чисел после запятой. Функция не изменяет значение баланса взятое из базы данных,
  а лишь подменяет отображение balance на cut_balance в list_display.
  """
  show_numbers = ledger_obj.currency.show_numbers
  balance = str(ledger_obj.balance)
  left_right_parts_balance = balance.split('.')
  left_part_balance = left_right_parts_balance[0]
  right_part_balance = left_right_parts_balance[1][:show_numbers]
  all_parts_balance = f'{left_part_balance},{right_part_balance}'
  return all_parts_balance
 cut_balance.short_description = gettext_lazy('Balance')


@admin.register(PhoneRent)
class PhoneRentAdmin(admin.ModelAdmin):
 fields = ['name', 'url', 'note', 'rent_service_type']
 list_display = ['name', 'url', 'rent_service_type']


@admin.register(PhoneRentAccount)
class PhoneRentAccountAdmin(admin.ModelAdmin):
 list_display = ['service', 'username', 'balance', 'account_state']
 fields = ['password', 'api_key', ] + list_display
 list_filter = ['account_state']
 actions = ['check_balance']
 change_list_template = 'admin/change_list_reload_ajax.html'

 def check_balance(self, request, queryset):
  rent_accounts = list(queryset.values())
  try:
   chain = random.choice(Chain.objects.filter(status=Chain.StatusChoice.READY.value))
   messages.success(request, f"Проверка баланса запущенна на {chain}")
  except IndexError as e:
   messages.error(request, "Для проверки баланса аккаунтов не найдена готовая цепочка")
   logger.exception(f'[check_balance]Catch error {e}')
   return

  for rent_service in rent_accounts:
   rent_service['rent_service_type'] = PhoneRent.objects.get(
    pk=rent_service['service_id']).rent_service_type.lower()

  PhoneRentAccount.check_balance_chain(chain.task_queue_name, rent_accounts).apply_async()

 check_balance.short_description = gettext_lazy('Check balance')