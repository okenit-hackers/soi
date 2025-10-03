class SoiException(Exception):
 pass


class SoiConfigException(SoiException):
 def __init__(self, config_name, config_value, message):
  self.config_name = config_name,
  self.config_value = config_value,
  self.message = message

 def __str__(self):
  return f'{self.__class__.__name__}: {self.message} ' \
     f'[`{self.config_name}`->`{self.config_value}`]'