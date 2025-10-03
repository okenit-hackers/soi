from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from anon_app.models import Chain
from lemmings_app.models import AccountPoolSetting
from functools import wraps


def disable_for_loaddata(signal_handler):
 """
 Decorator that turns off signal handlers when loading fixture data.
 """

 @wraps(signal_handler)
 def wrapper(*args, **kwargs):
  if kwargs.get('raw'):
   return
  signal_handler(*args, **kwargs)
 return wrapper


@receiver(post_save, sender=Chain, dispatch_uid='create_default_pool_settings')
@disable_for_loaddata
def create_default_pool_settings(sender, created, update_fields, instance: Chain, **kwargs):
 if created and instance.need_pull_accounts:
  AccountPoolSetting.set_default_to_chain(instance)


@receiver(pre_save, sender=AccountPoolSetting)
def check_changes_accountpoolsetting_fields(sender, instance, **kwargs):
 """
 Если любое поле класса AccountPoolSetting было изменено в админке, поле need_to_notification станет True
  """
 try:
  new_accountpoolsetting_obj = sender.objects.get(pk=instance.pk)
 except sender.DoesNotExist:
  pass # Object is new, so field hasn't technically changed, but you may want to do something else here.
 else:
  new_accountpoolsetting_fields = (new_accountpoolsetting_obj.needed_quantity,
           new_accountpoolsetting_obj.amount_of_attempts_to_create_accounts,
           new_accountpoolsetting_obj.sleep_between_runs,
           new_accountpoolsetting_obj.behavior_bot,
           new_accountpoolsetting_obj.is_need_set_behavior)
  old_accountpoolsetting_fields = (instance.needed_quantity,
           instance.amount_of_attempts_to_create_accounts,
           instance.sleep_between_runs,
           instance.behavior_bot,
           instance.is_need_set_behavior)
  if new_accountpoolsetting_fields != old_accountpoolsetting_fields:
   instance.need_to_notification = True