import logging

import pytz
from celery.result import AsyncResult
from celery.states import PENDING
from django.utils.encoding import escape_uri_path
from django_celery_beat.models import IntervalSchedule, CrontabSchedule, SolarSchedule, ClockedSchedule
from rest_framework import serializers
from rest_framework.fields import JSONField

from lemmings_app.fields import TimezoneField
from lemmings_app.models import LemmingsTask, BehaviorBots, BotAccount
from lemmings_app.tasks import internal_app
from lemmings_app.validators import ScheduleKindValidation

logger = logging.getLogger(__name__)


class LemmingsTaskSerializer(serializers.HyperlinkedModelSerializer):
 celery_task = serializers.SerializerMethodField()

 # noinspection PyMethodMayBeStatic,PyProtectedMember
 def get_celery_task(self, obj: LemmingsTask):
  from .views import LemmingsTaskViewSet
  host_prefix = ''
  request = self.context.get('request')

  if request is not None:
   url = escape_uri_path(request.path).rstrip('/')
   url = url[:url.rfind(f'/{LemmingsTaskViewSet.url_prefix}')]
   url = f'{request.scheme}://{request._get_raw_host()}{url}'
   host_prefix = url

  try:
   logger.info(f'Try to get celery task info: [{obj}]')
   from .views import CeleryTaskView
   result = AsyncResult(obj.task_id, backend=internal_app.backend)
   return {
    'url': f'{host_prefix}/{CeleryTaskView.url_prefix}/{result.id}/',
    'state': result.state or PENDING
   }
  except Exception as e:
   logger.warning(e, exc_info=True)
   return None

 kwargs = JSONField()
 result = JSONField()

 class Meta:
  model = LemmingsTask
  fields = ('url', 'action', 'kwargs', 'chain', 'result', 'celery_task')
  read_only_fields = ('result', 'celery_task')


class BotAccountSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = BotAccount
  fields = ['id', 'service', 'username', 'phone_number', 'chain_id', 'account_state',
     'created', 'changed', 'service_account']


class BehaviorBotsSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = BehaviorBots
  fields = ['name', 'interval', 'crontab', 'solar', 'clocked', 'one_off', 'enabled', 'start_time']

 validators = [ScheduleKindValidation.only_one_schedule_kind4periodic]


class IntervalScheduleViewSetSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = IntervalSchedule
  fields = '__all__'


class CrontabScheduleSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = CrontabSchedule
  fields = '__all__'

 timezone = TimezoneField([pytz.timezone(tz) for tz in pytz.common_timezones])


class SolarScheduleSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = SolarSchedule
  fields = '__all__'


class ClockedScheduleSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = ClockedSchedule
  fields = '__all__'
