from django.contrib import admin
from django.utils import timezone
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy

from notifications_app.forms import NotificationForm
from notifications_app.models import Notification, NotificationsEnabling


class ReadStatusFilter(admin.SimpleListFilter):
 title = gettext_lazy('Notification status')
 parameter_name = 'read_status'

 def lookups(self, request, model_admin):
  return (
   ('unread', gettext_lazy('Unread notifications')),
   ('read', gettext_lazy('Notifications read')),
  )

 def queryset(self, request, queryset):
  value = self.value()
  if value == 'read':
   return queryset.exclude(seen_date__isnull=True)
  elif value == 'unread':
   return queryset.filter(seen_date__isnull=True)
  return queryset.filter(user=request.user)

 def choices(self, changelist):
  """
  Убираем параметр all из фильтра, чтобы при нажатии "показать все уведомления" по умолчанию выгружались только
  непрочитанные сообщения, а не все.
  """
  for lookup, title in self.lookup_choices:
   if lookup != 'all': # Exclude the "All" option
    yield {
     'selected': self.value() == str(lookup),
     'query_string': changelist.get_query_string({self.parameter_name: lookup}),
     'display': title,
    }


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
 raw_id_fields = ["user"]
 change_list_template = 'admin/ajax_reload.html'
 list_filter = [ReadStatusFilter, "log_level", "send_date"]
 actions = ['seen_all']
 list_display = ["user", 'change_color_text', "seen_date", "created_date"]
 readonly_fields = ["seen_date", "send_date", 'created_date', 'error']
 form = NotificationForm
 fieldsets = (
  (None, {
   'fields': (
     'user', 'content', 'seen_date', 'send_date', 'created_date', 'log_level',
   )
  }),
  (gettext_lazy('error info'), {
   'classes': ('collapse',),
   'fields': ('error', 'traceback',),
  }),
 )

 def get_queryset(self, request):
  qs = super().get_queryset(request)
  read_status = request.GET.get('read_status')
  if read_status == 'read':
   return qs.exclude(user=request.user, seen_date__isnull=True)
  return qs.filter(user=request.user, seen_date__isnull=True)

 def seen_all(self, request, queryset):
  """
  all notification for a user will be set seen_date
  """
  filtered_queryset = queryset.filter(seen_date=None)
  filtered_queryset.update(
   seen_date=timezone.now()
   )
 seen_all.short_description = gettext_lazy('seen all')

 def change_color_text(self, request):
  """
  Меняет цвет текста, используя ns-style.css
  """
  log_level_choices = Notification.LogLevelChoice
  log_level = request.log_level

  if log_level == log_level_choices.COLOR_WARNING:
   color = 'orange'
  elif log_level == log_level_choices.COLOR_DANGER:
   color = 'red'
  else:
   color = 'normal'
  return mark_safe(f'<text class={color}>{request.content}</text>')

 change_color_text.short_description = gettext_lazy('Message')


@admin.register(NotificationsEnabling)
class NotificationsEnablingAdmin(admin.ModelAdmin):
 list_display = ['user', 'enabled']
 search_fields = ['user']