from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy
from rest_framework import serializers


class ScheduleKindValidation:
 schedule_types = ['interval', 'crontab', 'solar', 'clocked']

 @classmethod
 def _only_one_schedule(cls, value):
  selected_schedule_types = [s for s in cls.schedule_types if value.get(s)]

  err_msg = gettext_lazy('Only one of clocked, interval, crontab, or solar must be set')
  if len(selected_schedule_types) > 1:
   error_info = {}
   for selected_schedule_type in selected_schedule_types:
    error_info[selected_schedule_type] = [err_msg]
   raise serializers.ValidationError(error_info)

  # clocked must be one off task
  if value.get('clocked') and not value.get('one_off'):
   err_msg = gettext_lazy('clocked must be one off, one_off must set True')
   raise serializers.ValidationError(err_msg)

 @staticmethod
 def only_one_schedule_kind4periodic(value):
  selected_schedule_types = [s for s in ScheduleKindValidation.schedule_types if value.get(s)]

  if len(selected_schedule_types) == 0:
   raise serializers.ValidationError(
    gettext_lazy('One of clocked, interval, crontab, or solar must be set.')
   )

  ScheduleKindValidation._only_one_schedule(value)


def validate_date(value):
 msg = f'{gettext_lazy("wrong format, normal")}: 31.12.1990'

 if value.count('.') != 2:
  raise ValidationError(msg)

 value = list(map(int, value.split('.')))

 if not (1 <= value[0] <= 31):
  raise ValidationError(msg)

 if not (1 <= value[1] <= 12):
  raise ValidationError(msg)

 if not(1 <= value[2] <= 4096):
  raise ValidationError(msg)