from anon_app.models import Chain


class AnonAppException(Exception):
 pass


class OpenVPNFileDoesntExists(Exception):
 pass


class TooManyOpenVPNFiles(Exception):
 pass


class OpenVPNNeedRestart(Exception):
 pass


class SoiScriptKiddieException(Exception):
 pass


class CmdError(AnonAppException):
 pass


class MethodNotAvailable(AnonAppException):
 pass


class ZabbixSrvException(AnonAppException):
 pass


class ProxyCheckException(AnonAppException):
 pass


class ChainHasNoAliveProxies(AnonAppException):
 def __init__(self, chain: Chain):
  self.message = f'На цепочке - {chain} нет живых прокси. Обновите список прокси или удалите прокси'
  super().__init__(self.message)


class ServiceNotAvailableError(AnonAppException):
 pass