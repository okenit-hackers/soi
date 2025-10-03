import csv
import datetime
import time
from collections import Counter
from random import randint

from celery.result import AsyncResult
from celery.states import PENDING
from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from lemmings.botfarm import Shortcut
from lemmings.services_enum import Service

from lemmings_app.forms import AnonChainsForm, BotAccountForm, LemmingsTaskForm
from lemmings_app.models import *

logger = logging.getLogger(__name__)


class LemmingsTaskAdmin(admin.ModelAdmin):
 list_filter = ['action', 'chain']
 list_display = ['id', 'celery_task'] + list_filter + ['result']
 form = LemmingsTaskForm

 # noinspection PyBroadException
 @admin.display(description=gettext_lazy('state'))
 def celery_task(self, obj: LemmingsTask):
  try:
   return AsyncResult(obj.task_id).state or PENDING
  except Exception:
   return 'unknown'

 def save_model(self, request, obj, form, change):
  if change:
   return super(LemmingsTaskAdmin, self).save_model(request, obj, form, change)

  from lemmings_app.utils import validate_lmgs_task, run_celery_task

  validate_lmgs_task(obj)

  how_much_to_create = form.cleaned_data['hom_much_to_create']
  if how_much_to_create == 1:
   obj.save()
   run_celery_task(obj)
   return

  start_date = datetime.datetime.now()
  try:
   service = Shortcut.resolver(obj.action)[3]
   sleep_between_runs = AccountPoolSetting.objects.get(
    chain=obj.chain,
    resource_name=service.name
   ).sleep_between_runs
  except Exception as E:
   logger.warning(f'error {E}', exc_info=True)
   sleep_between_runs = 60 * 3

  for _ in range(how_much_to_create):
   copied_obj = copy.deepcopy(obj)
   copied_obj.save() # need for not none obj.pk
   run_celery_task(copied_obj, periodic=True, start_date=start_date)
   start_date += datetime.timedelta(seconds=sleep_between_runs)


class ShowBotsWithLinked(admin.SimpleListFilter):
 """Filter bots by service and show accounts from which bots are in dependency.

 Include services which use dependency aacounts: Instagram, Linkedin, Myspace, Reddit.
 """

 title = gettext_lazy('Show accounts with linked accounts')
 parameter_name = 'bots_with_linked_accounts'

 def lookups(self, request, model_admin) -> list[tuple[str, str]]:
  """Get list of tuples with request parameters and verbose names for them.

  Args:
   request: WSGI django request.
   model_admin: BotAccountAdmin.

  Returns:
   List of request parameters and their human-readable representation.
  """
  return [
   (Service.INSTAGRAM.name, Service.INSTAGRAM.value),
   (Service.LINKEDIN.name, Service.LINKEDIN.value),
   (Service.MYSPACE.name, Service.MYSPACE.value),
   (Service.REDDIT.name, Service.REDDIT.value),
  ]

 def queryset(self, request, queryset):
  """Show bots of chosen service, and accounts from which bots are in dependency.

  Args:
   request: WSGI django request.
   queryset: all accounts which must be filtered.

  Returns:
   Queryset with filtered bot accounts.
  """
  linked_account_ids = []
  if not self.value():
   return
  bot_accounts = queryset.filter(service=self.value())
  if bot_accounts.exists():
   for bot_account in bot_accounts:
    dependency_account = bot_account.extra.get('reg_required_account')
    if dependency_account:
     linked_account_ids.append(dependency_account[0]['pk'])
  return bot_accounts | queryset.filter(pk__in=linked_account_ids)


@admin.register(BotAccount)
class BotAccountAdmin(admin.ModelAdmin):
 form = BotAccountForm
 actions = ['check_login', 'reserve_accounts', 'set_ready', 'export_to_csv', 'export_to_human_csv', 'assign_anon_chain']

 def get_actions(self, request):
  actions = super().get_actions(request)
  filters = request.GET.get('service__exact', '')
  if 'TELEGRAM_GROUPS' in filters:
   actions['push_to_tag_admin'] = (self.push_to_tag_admin, 'push_to_tag_admin', self.push_to_tag_admin.__doc__)
  return actions

 def push_to_tag_admin(self, request, queryset, *args):
  """Отправить ботов на сервер TAG"""
  accounts_without_chain = []
  for ba in args[0]:
   logger.info(f'Start push {ba.username} to TAG')
   if ba.chain:
    tasks_chain = ba.push_ba_to_tag()
    tasks_chain.apply_async()
    continue
   accounts_without_chain.append(str(ba.pk))
   logger.info('Аккаунт {0} не отправлен на сервер TAG из-за отсутствия цепочки анонимизации'.format(
    ba.username,
   )
   )
  warning_message = (
   'Аккаунты с ID {0} не отправлены на сервер TAG из-за отсутствия цепочек анонимизации'.format(
    ', '.join(accounts_without_chain),
   )
  )
  if accounts_without_chain:
   messages.warning(request, warning_message)
   logger.warning(warning_message)

 push_to_tag_admin.short_description = gettext_lazy('push_to_tag')

 def export_to_csv(self, request, queryset):
  opts = self.model._meta
  response = HttpResponse(content_type='text/csv')
  response['Content-Disposition'] = 'attachment;' 'filename{}.csv'.format(opts.verbose_name)
  writer = csv.writer(response)
  # Write a first row with header information
  fields = [
   'service', 'username', 'password', 'phone_number', 'email', 'sex', 'date_of_birth',
   'first_name', 'last_name', 'extra', 'api_id', 'api_hash', 'api_session',
  ]
  # Write data rows
  for obj in queryset:
   data_row = []
   for field in fields:
    value = getattr(obj, field)
    if field == 'extra':
     value = json.dumps(value)

    data_row.append(value)
   writer.writerow(data_row)

  return response

 export_to_csv.short_description = gettext_lazy('Export to CSV') # short description

 def export_to_human_csv(self, request, queryset):
  metadata = self.model._meta
  response = HttpResponse(content_type='text/csv')
  response['Content-Disposition'] = 'attachment;' 'filename={}.csv'.format(metadata.verbose_name)
  writer = csv.writer(response)
  fields = [
   'service', 'username', 'password', 'phone_number', 'email', 'sex', 'date_of_birth',
   'first_name', 'last_name', 'api_id', 'api_hash', 'api_session',
  ]
  writer.writerow([metadata.get_field(field).verbose_name for field in fields])
  for obj in queryset:
   data_row = []
   for field in fields:
    value = getattr(obj, field)
    if field == 'sex':
     if value == '':
      value = 'Не определено'
     else:
      value = 'М' if int(value) == 0 else 'Ж'
    data_row.append(value)
   writer.writerow(data_row)

  return response

 export_to_human_csv.short_description = gettext_lazy('Export to human CSV')

 def check_login(self, request, queryset):
  accounts_without_chain = []
  for ba in queryset:
   if ba.service_account:
    continue
   logger.info(f'Start to login for {ba.username}')
   if ba.chain:
    time.sleep(randint(1, 300)) # Рандомная задержка до 5 минут, чтобы предотвратить массовый логин
    tasks_chain = ba.make_login_chain()
    tasks_chain.apply_async()
    continue
   accounts_without_chain.append(str(ba.pk))
   logger.info('Аккаунт {0} не был проверен из-за отсутствия цепочки анонимизации'.format(
     ba.username,
    )
   )
  warning_message = (
   'Аккаунты с ID {0} не были проверены из-за отсутствия цепочек анонимизации'.format(
    ', '.join(accounts_without_chain),
   )
  )
  if accounts_without_chain:
   messages.warning(request, warning_message)
   logger.warning(warning_message)

 check_login.short_description = gettext_lazy('check_login')

 def set_ready(self, request, queryset):
  queryset.update(account_state=BotAccount.STATE.READY)

 set_ready.short_description = gettext_lazy('set ready')

 def reserve_accounts(self, request, queryset):
  state = BotAccount.STATE
  queryset.update(account_state=state.ACCOUNT_RESERVED)
  messages.info(request, f'Успешно зарезервированно аккаунтов: {queryset.count()} ')
  return request

 reserve_accounts.short_description = gettext_lazy('Reserve accounts')

 fieldsets = (
  (None, {
   'fields': [
    'chain', 'service', 'username', 'password', 'phone_number', 'location', 'needs_new_mail', 'email',
    'behavior_bot', 'sex', 'date_of_birth', 'first_name', 'last_name', 'api_id', 'api_hash', 'api_session',
    'service_account', 'enable_behavior_emulation', 'dependency', 'create_type',
    'account_state', 'successful_auth_date', 'created',
    'changed', 'view_related_accounts', 'login_info', 'reg_info',
   ]
  }),
  (gettext_lazy('error info'), {
   'classes': ('collapse', ),
   'fields': ('last_error', 'last_traceback', ),
  }),
 )

 filterable_fields = ['service', 'created', 'changed', 'chain', 'account_state', 'dependency']
 list_filter = filterable_fields + [ShowBotsWithLinked]
 list_display = ['id'] + filterable_fields + ['behavior_bot', 'successful_auth_date', 'first_name', 'last_name',
            'sex', 'username', 'password', 'phone_number', 'email',
            'enable_behavior_emulation', ]
 search_fields = ['username', 'phone_number', 'email', 'behavior_bot__name', ]
 change_list_template = 'admin/import_bots_button_template.html'

 readonly_fields = [
  'account_state', 'created', 'login_info', 'reg_info',
  'last_error', 'successful_auth_date', 'dependency',
  'create_type', 'changed', 'view_related_accounts',
 ]

 exclude = ['extra', ]

 def get_fieldsets(self, request, obj=None):
  # поле с балансом будет добавляться только для аккаунтов, зареганных для telegram_bots сервиса
  fieldsets = copy.deepcopy(super().get_fieldsets(request, obj))

  if obj is not None and obj.service == Service.TELEGRAM_BOTS.name:
   fieldsets[0][1]['fields'].append('tg_balance')
   self.readonly_fields.append('tg_balance')

  return fieldsets

 def get_readonly_fields(self, request, obj=None):
  if obj: # editing an existing object
   return self.readonly_fields + [
    'service', 'dependency', 'first_name', 'last_name', 'sex', 'username',
    'password', 'phone_number', 'email', 'sex', 'date_of_birth', 'first_name', 'last_name',
    'api_id', 'api_hash', 'api_session', 'service_account',
   ]
  return self.readonly_fields

 def assign_anon_chain(self, request, bot_accounts):
  """Source: https://habr.com/ru/post/140409/"""
  form = None
  if 'apply' in request.POST:
   form = AnonChainsForm(request.POST)

   if form.is_valid():
    anon_chain = form.cleaned_data['anon_chain']
    bot_accounts.update(chain=anon_chain)
    messages.success(request, f'Цепочка анонимизации {anon_chain.title} применена к аккаунтам.')
    return HttpResponseRedirect(request.get_full_path())

  services = []
  for bot_account in bot_accounts:
   acc_service = bot_account.service
   translated_service = Service.__getattr__(acc_service)
   services.append(translated_service.value)
  accounts_repr = dict(Counter(services))

  if not form:
   form = AnonChainsForm(initial={'_selected_action': bot_accounts.values_list('id', flat=True)})

  return render(request, 'admin/anon_chains.html',
      {'accounts_repr': accounts_repr, 'form': form, 'title': 'Назначить цепочку анонимизации'})

 assign_anon_chain.short_description = gettext_lazy('Assign anon chain')


@admin.register(AccountPoolSetting)
class AccountPoolSettingAdmin(admin.ModelAdmin):
 list_filter = ['service', 'chain']
 list_display = ['id'] + list_filter + ['needed_quantity', 'is_need_set_behavior',
            'amount_of_attempts_to_create_accounts',
            'sleep_between_runs', 'last_triggered_at', 'behavior_bot',
            'need_to_notification']
 list_editable = [
  'needed_quantity', 'amount_of_attempts_to_create_accounts', 'sleep_between_runs', 'behavior_bot',
  'is_need_set_behavior', 'need_to_notification',
 ]
 readonly_fields = ['last_triggered_at']
 actions = ['turn_on_behavior', 'turn_off_behavior', 'turn_on_send_notifications', 'turn_off_send_notifications']

 def turn_on_behavior(self, request, account_pools):
  account_pools.update(is_need_set_behavior=True)
 turn_on_behavior.short_description = gettext_lazy('Turn on behavior')

 def turn_off_behavior(self, request, account_pools):
  account_pools.update(is_need_set_behavior=False)
 turn_off_behavior.short_description = gettext_lazy('Turn off behavior')

 def turn_on_send_notifications(self, request, queryset):
  queryset.update(need_to_notification=True)
 turn_on_send_notifications.short_description = gettext_lazy('Enable notifications')

 def turn_off_send_notifications(self, request, queryset):
  queryset.update(need_to_notification=False)
 turn_off_send_notifications.short_description = gettext_lazy('Disable notifications')


@admin.register(BehaviorBots)
class BehaviorBotsAdmin(admin.ModelAdmin):
 list_display = ['id', 'name', 'enabled', 'one_off']
 actions = ['turn_on_behavior_bots', 'turn_off_behavior_bots']

 fieldsets = (
  (None, {
   'fields': ('name', 'enabled',),
   'classes': ('extrapretty', 'wide'),
  }),
  (gettext_lazy('Schedule'), {
   'fields': ('interval', 'crontab', 'solar', 'clocked',
       'start_time', 'one_off'),
   'classes': ('extrapretty', 'wide'),
  }),
 )

 def turn_on_behavior_bots(self, request, queryset):
  for behavior_bots in queryset:
   behavior_bots.enabled = True
   behavior_bots.save()
 turn_on_behavior_bots.short_description = gettext_lazy('Turn on behaviors bots')

 def turn_off_behavior_bots(self, request, queryset):
  for behavior_bots in queryset:
   behavior_bots.enabled = False
   behavior_bots.save()
 turn_off_behavior_bots.short_description = gettext_lazy('Turn off behaviors bots')

 def delete_model(self, request, obj):
  periodic_task = obj.periodic_task
  if periodic_task:
   obj.enabled = False
   obj.save()
  return super(BehaviorBotsAdmin, self).delete_queryset(request, obj)

 def delete_queryset(self, request, queryset):
  for behavior_bots in queryset:
   periodic_task = behavior_bots.periodic_task
   if periodic_task:
    behavior_bots.enabled = False
    behavior_bots.save()
  return super(BehaviorBotsAdmin, self).delete_queryset(request, queryset)