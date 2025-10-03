from enum import Enum

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Q
from django.conf import settings
from django.utils.translation import gettext_lazy


class NotSeenQuerySet(models.QuerySet):
 """
 this is shortcut to filter notifications that not seen yet
 """

 def not_seen(self, user):
  return self.filter(Q(send_date__isnull=True) & Q(user=user))


class Notification(models.Model):

 user = models.ForeignKey(
  settings.AUTH_USER_MODEL,
  related_name="notifications",
  related_query_name="user",
  on_delete=models.CASCADE,
  verbose_name=gettext_lazy('user')
 )

 # нельзя удалить из-за старых миграций
 class TextColors(Enum):
  COLOR_WARNING = '#f39c12'
  COLOR_DANGER = '#f56954'
  COLOR_INFO = '#00c0ef'
  COLOR_SUCCESS = '#00a65a'
  COLOR_PRIMARY = '#3c8dbc'
  COLOR_GRAY = '#d2d6de'
  COLOR_BLACK = '#111111'

 # нельзя удалить из-за старых миграций
 COLOR_CHOICES = (
  (TextColors.COLOR_DANGER.value, gettext_lazy('Danger')),
  (TextColors.COLOR_WARNING.value, gettext_lazy('Warning')),
  (TextColors.COLOR_SUCCESS.value, gettext_lazy('Success')),
  (TextColors.COLOR_INFO.value, gettext_lazy('Info')),
  (TextColors.COLOR_PRIMARY.value, gettext_lazy('Primary')),
  (TextColors.COLOR_GRAY.value, gettext_lazy('Gray')),
  (TextColors.COLOR_BLACK.value, gettext_lazy('Black')),
 )

 class LogLevelChoice(models.TextChoices):
  COLOR_WARNING = '#f39c12', gettext_lazy('Warning'),
  COLOR_DANGER = '#f56954', gettext_lazy('Danger'),
  COLOR_INFO = '#00c0ef', gettext_lazy('Info')
  COLOR_SUCCESS = '#00a65a', gettext_lazy('Success')

 log_level = models.CharField(
  max_length=7,
  choices=LogLevelChoice.choices,
  default=LogLevelChoice.COLOR_INFO.value,
  verbose_name=gettext_lazy('log level')
 )

 content = models.TextField(
  max_length=512,
  verbose_name=gettext_lazy('content')
 )

 seen_date = models.DateTimeField(
  blank=True,
  null=True,
  verbose_name=gettext_lazy('seen date'),
 )

 send_date = models.DateTimeField(
  blank=True,
  null=True,
  verbose_name=gettext_lazy('send date'),
 )

 created_date = models.DateTimeField(
  auto_now=True,
  verbose_name=gettext_lazy('created date')
 )

 error = models.CharField(blank=True, max_length=1024, verbose_name=gettext_lazy('error'))

 traceback = models.TextField(
  blank=True, verbose_name=gettext_lazy('traceback')
 )

 objects = NotSeenQuerySet.as_manager()

 def __str__(self):
  return f'Уведомления пользователя - {self.user}, номер уведомления - {self.pk}'

 @classmethod
 def send_to_all(cls, content, log_level, error=None, traceback=None):
  """
  use this for send notification to all users
  :param content: content of notification
  :param log_level: log_level number (hex)
  :param error: str error
  :param traceback: str traceback

  :return: instances of Notification class (list)
  """
  if error:
   error = str(error[:1000])

  objs = []
  if len(content) >= 512:
   content = content[:500] + '...'

  if traceback is not None and error is not None:
   for user in User.objects.all():
    objs.append(cls(content=content, log_level=log_level, user=user, error=error, traceback=traceback))
  elif error is not None:
   for user in User.objects.all():
    objs.append(cls(content=content, log_level=log_level, user=user, error=error))
  else:
   for user in User.objects.all():
    objs.append(cls(content=content, log_level=log_level, user=user))
  cls.objects.bulk_create(objs)

 @classmethod
 def send_to_current_user(cls, user: User, content, log_level):
  """
  use this for send notification to to current user
  :param user: user we want to send notification
  :param content: content of notification
  :param log_level: log_level number (hex)

  :return: instances of Notification class (list)
  """
  if len(content) >= 512:
   content = content[:500] + '...'

  notification = cls(content=content, log_level=log_level, user=user)
  notification.save()

 class Meta:
  ordering = ['-id']
  verbose_name = gettext_lazy('Notification')
  verbose_name_plural = gettext_lazy('Notifications')


class NotificationsEnabling(models.Model):
 """Model of notification enabling for current user."""

 user = models.OneToOneField(
  User,
  on_delete=models.CASCADE,
  verbose_name=gettext_lazy('user'),
 )
 enabled = models.BooleanField(default=True, verbose_name=gettext_lazy('enabled'))

 class Meta:
  constraints = [models.UniqueConstraint(
   fields=['user', 'enabled'], name='unique_notifications_enabling',
  )]
  ordering = ['-id']
  verbose_name = gettext_lazy('Notifications enabling')
  verbose_name_plural = gettext_lazy('Notifications enabling')