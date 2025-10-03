from abc import ABC, abstractmethod


class BaseBehaviorEmulator(ABC):
 base_url = ''

 @abstractmethod
 def login(self, **kwargs):
  pass

 @abstractmethod
 def emulate_behavior(self, **kwargs):
  pass