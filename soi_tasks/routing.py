import logging

from django.conf import settings

from soi_tasks.exceptions import CeleryRoutingError

logger = logging.getLogger(__name__)


class TaskRouter:
 internal_celery_queue_name = settings.INTERNAL_CELERY_QUEUE_NAME

 def route_for_task(self, task, args, kwargs):
  log_info = f'[queue_name={kwargs.get("queue_name")}]' if not kwargs.get('is_internal', False) else f'[{self.__class__.__name__}]'
  logger.info(f'Try to route {kwargs.get("task_identifier")} {log_info}')

  if kwargs.get('task_identifier') is None:
   raise CeleryRoutingError('Need task identifier')

  if kwargs.get('is_internal', False):
   logger.info(
    f'{kwargs["task_identifier"]} is routed to default internal '
    f'queue `{self.internal_celery_queue_name}` {log_info}'
   )
   queue_name = self.internal_celery_queue_name
   if kwargs.get('is_priority', False):
    queue_name = f'priority_{self.internal_celery_queue_name}'
   return {'queue': queue_name}

  if not kwargs.get('queue_name'):
   raise CeleryRoutingError('Need queue_name id (if it\'s internal task use is_internal option)')

  logger.info(
   f'{kwargs["task_identifier"]} is routed to {kwargs.get("queue_name")} queue {log_info}'
  )
  queue_name = kwargs.get('queue_name')
  if kwargs.get('is_priority', False):
   queue_name = f'priority_{queue_name}'
  return {'queue': queue_name}


class BotfarmTaskRouter(TaskRouter):
 internal_celery_queue_name = settings.INTERNAL_CELERY_BOTFARM_QUEUE_NAME