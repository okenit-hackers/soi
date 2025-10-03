import logging
from importlib import import_module


logger = logging.getLogger(__name__)


class BehaviorServiceController:

 @classmethod
 def _get_class(cls, module: str, class_name: str):
  try:
   _class = getattr(import_module(module), class_name, None)
  except ModuleNotFoundError as e:
   logger.warning(f'Not found module [{module}], {e}')
   _class = None

  if _class is None:
   logger.warning(f'Class [{class_name}] not found in module [{module}]')

  return _class

 @classmethod
 def get_behavior_emulator_controller(cls, service: str):
  logger.info(f'Getting service behavior emulator class for service [{service}]')
  return cls._get_class(
   f'stereotypes_generator.behavior_emulator.{service.lower().strip()}.{service.lower().strip()}',
   f'{"".join(char.title() for char in service.split("_"))}BehaviorEmulator'
  )