import logging
import os
import re
import shlex
import shutil
import time
from abc import abstractmethod, ABC, ABCMeta
from collections import OrderedDict
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import List, Union, Tuple, Dict, Iterable

from ansible_runner.interface import init_runner as init_ansible_playbook_runner
from fabric import Connection
from invoke import Context, Result
from retry import retry

from anon_app.conf import settings
from anon_app.exceptions import CmdError
from anon_app.models import Node, Edge, Chain, OpenVPNClient, Proxy
from soi_app.settings import SCRAPER_SELENIUM_IDE_TEMPLATES_DIR, DATA_PREFIX

logger = logging.getLogger(__name__)


# noinspection SpellCheckingInspection
class AnsibleRunnerStatus:
 UNSTARTED = 'unstarted'
 STARTING = 'starting'
 RUNNING = 'running'
 CANCELED = 'canceled'
 SUCCESSFUL = 'successful'
 TIMEOUT = 'timeout'
 FAILED = 'failed'


class BaseCmd(ABC):
 @property
 @abstractmethod
 def env(self) -> dict:
  pass

 @property
 @abstractmethod
 def _required_fields(self) -> set:
  pass

 @property
 def _hash_fields(self) -> Union[set, None]:
  return None

 @abstractmethod
 def serialize(self) -> Tuple[str, dict]:
  pass

 @classmethod
 @abstractmethod
 def deserialize(cls, cmd: str, data: dict) -> Union['SSHCopyIdCmd', 'None']:
  pass

 # noinspection PyMethodMayBeStatic
 def _execute(self, ctx: Context, cmd: str, env: dict, hide=True, warn=True) -> Tuple[Result, bool]:
  r = ctx.run(cmd, env=env, hide=True, warn=True)
  return r, r.ok

 @property
 def runtime_env(self):
  # noinspection SpellCheckingInspection
  return {**(self.env if self.env is not None else {}), 'SOICMDFLAG': str(hash(self))}

 @retry(Exception, tries=4, delay=2)
 def execute(self, context: Context = None) -> Result:
  """
  Запуск выполнения комманды

  :param context: контекст выполнения
  :return: результат работы
  """

  cmd, _ = self.serialize()

  logger.info(f'[{self.__class__.__name__}][call][{hash(self)}]: `{cmd}`')

  ctx = context if context is not None else Context()
  r, is_ok = self._execute(ctx=ctx, cmd=cmd, env=self.runtime_env, hide=True, warn=True)

  if not is_ok:
   msg = f'[{self.__class__.__name__}][called][{hash(self)}]: {self._build_error_message(r)}'
   logger.error(msg)
   raise CmdError(msg)

  msg = self._build_ok_message(r)
  logger.info(f'[{self.__class__.__name__}][called][{hash(self)}]: {msg}')
  return r

 def kill(self) -> 'KillProcCmd':
  # noinspection SpellCheckingInspection
  return KillProcCmd(f'SOICMDFLAG={self.__hash__()}')

 @staticmethod
 def _build_error_message(result: Result) -> str:
  # noinspection SpellCheckingInspection
  return f'EXIT-CODE: {result.return_code} | STDERR: `' + result.stderr.replace('\n', ' <br> ') \
   .replace('\r', ' <crrg-rtrn> ') + f'` | CMD: `{result.command}` '

 @staticmethod
 def _build_ok_message(result: Result) -> str:
  # noinspection SpellCheckingInspection
  return f'STDOUT: `' + result.stdout.replace('\n', ' <br> ') \
   .replace('\r', ' <crrg-rtrn> ') + f'` | CMD: `{result.command}`'

 def __str__(self):
  serialized = self.serialize()
  return f'{self.__class__.__name__}: `{serialized[0]}` | DATA: {serialized[1]}'

 def __repr__(self):
  return self.__str__()

 def __copy__(self):
  return self.__class__.deserialize(*self.serialize())

 def __or__(self, other):
  if isinstance(other, BaseCmd):
   return CmdChain(self, other)
  elif isinstance(other, CmdChain):
   chain = other.__copy__()
   chain.todo.insert(0, self)
   return chain

  raise TypeError(f'expected instance of CmdChain or BaseCmd but got {type(other)}')

 def __enter__(self):
  return self

 def __exit__(self, type_, value, traceback):
  if type(self) == KillProcCmd:
   return

  self.kill().execute()

 def __hash__(self) -> int:
  data = ''
  fields = self._hash_fields if self._hash_fields is not None else self._required_fields

  for field_name in sorted(fields):
   value = getattr(self, field_name)

   if isinstance(value, dict):
    value = OrderedDict(value)
    value = '<' + '|'.join(f'{k}:{v}' for k, v in value.items()) + '>'
   elif isinstance(value, (list, tuple, set)):
    value = sorted(value, key=str)
    value = '[' + '|'.join(f'{v}' for v in value) + ']'

   data += str(value)

  data += self.__class__.__qualname__
  data = data.encode()

  return hash(sha256(data).digest())

 def __eq__(self, other):
  if other is None or not isinstance(other, self.__class__):
   return False

  fields = self._hash_fields if self._hash_fields is not None else self._required_fields

  return all(
   getattr(self, field_name) == getattr(other, field_name)
   for field_name in fields
  )


class CmdChain:
 def __init__(self, *args: Union[BaseCmd, Iterable[BaseCmd]]):
  if len(args) == 1 and isinstance(args[0], Iterable):
   args = list(args[0])

  self.todo: List[BaseCmd] = list(args)
  self.context = Context()
  self.results = {}

 def run(self, raise_exc=True, is_need_exit=True) -> Union[Dict['BaseCmd', Result], 'None']:
  for cmd in self.todo:
   try:
    self.results[cmd] = cmd.execute(self.context)
   except Exception as e:
    if raise_exc:
     raise e
    logger.warning(f'Can n\'t execute {cmd.__class__.__name__}: {e}')
    if is_need_exit:
     return

  return self.results

 def set_context(self, context: Context):
  self.context = context

 def serialize(self) -> List[Tuple[str, dict, str]]:
  script = []

  for cmd in self.todo:
   script.append((*cmd.serialize(), cmd.__class__.__name__))

  # noinspection PyTypeChecker
  return script

 def kill(self) -> 'CmdChain':
  """Убивает процессы"""
  return CmdChain(cmd.kill() for cmd in self.todo)

 @classmethod
 def deserialize(cls, script_data: List[Tuple[str, dict, str]]) -> Union['CmdChain', 'None']:
  known_cmd = [
   obj for obj in locals().values()
   if type(obj) == ABCMeta and issubclass(obj, BaseCmd) and obj != BaseCmd
  ]

  instance = cls()

  # noinspection PyBroadException
  try:
   for cmd_line, env, class_name in script_data:
    clazz = locals()[class_name]
    assert type(clazz) == ABCMeta and issubclass(clazz, BaseCmd) and clazz != BaseCmd
    # noinspection PyArgumentList
    cmd = clazz(cmd_line, env)
    instance |= cmd
  except Exception:
   return None

 def __or__(self, other):
  if isinstance(other, BaseCmd):
   instance = self.__copy__()
   instance.todo.append(other)
   return instance
  elif isinstance(other, CmdChain):
   instance = self.__copy__()
   instance.todo += other.todo
   return instance

  raise TypeError(f'expected instance of CmdChain or BaseCmd but got {type(other)}')

 def __str__(self):
  return '<CmdChain: ' + ' | '.join(c.__class__.__name__ for c in self.todo) + ' >'

 def __repr__(self):
  return self.__str__()

 def __copy__(self):
  instance = self.__class__(*self.todo)
  instance.set_context(self.context)
  instance.results = {**self.results}
  return instance

 def __eq__(self, other):
  if other is None or not isinstance(other, self.__class__):
   return False

  return self.todo == other.todo

 def __hash__(self):
  hash_ = 0
  for cmd in self.todo:
   hash_ += hash(cmd) % 0xffffffffffffffffffffffffffffffff # 2**128


class SSHCopyIdCmd(BaseCmd):
 _required_fields = {
  'host', 'port', 'username', 'password',
  'public_key_path', 'password_env_name', 'proxy_command_cmd'
 }

 def __init__(self, node: Node = None, is_forwarded=True, proxy_command: BaseCmd = None, **kwargs):
  """
  `sshpass -p "$p1234" ssh-copy-id -oStrictHostKeyChecking=no -i "key.pub" -p 22 user@host;`

  Аргументы берутся либо из node и proxy_command (необязательный арг.), либо из kwargs.
  Названия необходимых (только если node не задан) именнованных аргументов:

  :param is_forwarded: проброшенное ли соединение до узла (только при использовании node)
  :param host: адрес узла
  :param port: порт ssh интерфейса
  :param username: имя пользователя
  :param password: пароль пользователя
  :param public_key_path: путь до публичного ключа
  :param password_env_name: имя переменой окружения используемой для передачи пароля
  :param proxy_command_cmd: строка с командой проксирования
  """

  if node is None and (self._required_fields - set(kwargs.keys())) - {'proxy_command_cmd'}:
   raise TypeError(
    f'__init__() missing required arguments: node or {", ".join(self._required_fields)}'
   )

  self.host = ('localhost' if is_forwarded else node.server.ssh_ip) \
   if node is not None else kwargs['host']
  self.port = int(
   (node.ssh_proc_port if is_forwarded else node.server.ssh_port)
   if node is not None else kwargs['port']
  )
  self.username = node.server.server_account.username if node is not None \
   else kwargs['username']
  self.password = node.server.server_account.password if node is not None \
   else kwargs['password']
  self.public_key_path = node.id_rsa_pub.path if node is not None \
   else kwargs['public_key_path']
  self.password_env_name = f'p{node.id}' if node is not None \
   else kwargs['password_env_name']
  self.proxy_command_cmd, _ = proxy_command.serialize() if proxy_command is not None \
   else (kwargs.get('proxy_command_cmd'), None)

 @property
 def env(self) -> dict:
  return {self.password_env_name: self.password}

 def serialize(self) -> Tuple[str, dict]:
  cmd = f'sshpass -p "${self.password_env_name}" ssh-copy-id -oStrictHostKeyChecking=no ' \
    f'-i "{self.public_key_path}" -p {self.port} {self.username}@{self.host}' + \
    (f' -oProxyCommand="{self.proxy_command_cmd}";' if self.proxy_command_cmd is not None else ';')
  return cmd, {'password': self.password}

 @classmethod
 def deserialize(cls, cmd: str, data: dict) -> Union['SSHCopyIdCmd', 'None']:
  match = re.match(
   '^sshpass -p "\\$(p[0-9]*)" ssh-copy-id -oStrictHostKeyChecking=no '
   '-i "(.*)" -p ([0-9]*) (.*)@(.*);$',
   cmd
  )

  match = re.match(
   '^sshpass -p "\\$(p[0-9]*)" ssh-copy-id -oStrictHostKeyChecking=no '
   '-i "(.*)" -p ([0-9]*) (.*)@(.*) -oProxyCommand="(.*)";$',
   cmd
  ) if match is None else match

  if match is None or 'password' not in data:
   return None

  return cls(
   password_env_name=match.group(1),
   public_key_path=match.group(2),
   port=match.group(3),
   username=match.group(4),
   host=match.group(5),
   proxy_command_cmd=match.group(6) if len(match.groups()) > 5 else None,
   **data
  )


class AutoSSHCmd(BaseCmd):
 _required_fields = {
  'out_host', 'out_port', 'out_username',
  'out_private_key_path', 'remote_in_host', 'remote_in_port',
  'local_in_host', 'local_in_port', 'proxy_command_cmd'
 }

 def __init__(
   self, edge: Edge = None, is_forwarded=True,
   proxy_command: BaseCmd = None, route=1,
   local_in_host='localhost', **kwargs
 ):
  # noinspection SpellCheckingInspection
  """
  `autossh -M 0 -oStrictHostKeyChecking=no -fN user@host -p 44671 -L localhost:11:host_2:22 -i "key";`

  :param route: определяет в какую сторону пробрасывается порт: 1 это флаг `-L`, 0 - `-R`

  Аргументы берутся либо из edge, либо из kwargs.
  Названия необходимых (только если node не задан) именнованных аргументов:

  :param is_forwarded: проброшен ли порт исходящего узела (является ли соединение первым в цепочке)
       (необходим при использовании edge)
  :param out_host: адрес первого в соединении узла
  :param out_port: порт ssh интерфейса первого в соединении узла
  :param out_username: имя пользователя первого в соединении узла
  :param out_private_key_path: путь до публичного ключа первого в соединении узла
  :param remote_in_host: адрес второго в соединении узла
  :param remote_in_port: порт ssh интерфейса второго в соединении узла
  :param local_in_host: адрес хоста куда надо пробросить порт
  :param local_in_port: порт ssh интерфейса который будет поднят локально
        после подключени ко второму в соединении узлу
  :param proxy_command_cmd: строка с командой проксирования
  """

  if edge is None and (self._required_fields - set(kwargs.keys())) - {'proxy_command_cmd', 'local_in_host'}:
   raise TypeError(
    f'__init__() missing required arguments: edge or {", ".join(self._required_fields)}'
   )

  self.out_host = ('localhost' if is_forwarded else edge.out_node.server.ssh_ip) \
   if edge is not None else kwargs['out_host']
  self.out_port = int(
   (edge.out_node.ssh_proc_port if is_forwarded else edge.out_node.server.ssh_port)
   if edge is not None else kwargs['out_port']
  )
  self.out_username = edge.out_node.server.server_account.username if edge is not None \
   else kwargs['out_username']
  self.out_private_key_path = edge.out_node.id_rsa.path if edge is not None \
   else kwargs['out_private_key_path']
  self.remote_in_host = (
   edge.in_node.ovpn_srv_ip if edge.protocol == Edge.ProtocolChoice.VPN else edge.in_node.server.ssh_ip
  ) if edge is not None else kwargs['remote_in_host']

  self.remote_in_host = kwargs.get('remote_in_host')
  if self.remote_in_host is None:
   if edge is None:
    raise ValueError('Specify remote_in_host or edge')
   self.remote_in_host = edge.in_node.server.ssh_ip

  self.remote_in_port = int(edge.in_node.server.ssh_port if edge is not None else kwargs['remote_in_port'])
  self.local_in_host = local_in_host
  self.local_in_port = int(edge.in_node.ssh_proc_port if edge is not None else kwargs['local_in_port'])
  self.proxy_command_cmd, _ = proxy_command.serialize() if proxy_command is not None \
   else (kwargs['proxy_command_cmd'] if 'proxy_command_cmd' in kwargs else None, None)
  self.route = route

 @property
 def env(self) -> dict:
  return {}

 def serialize(self) -> Tuple[str, dict]:
  forward_to = f'{self.local_in_host}:{self.local_in_port}'
  forward_from = f'{self.remote_in_host}:{self.remote_in_port}'

  if self.route == 0:
   forward_to, forward_from = forward_from, forward_to

  # noinspection SpellCheckingInspection
  cmd = f'autossh -M 0 -oStrictHostKeyChecking=no ' \
    f'-fN {self.out_username}@{self.out_host} -{"L" if self.route else "R"} {forward_to}:{forward_from}' \
    f' -p {self.out_port} -i "{self.out_private_key_path}"' + \
    (f' -oProxyCommand="{self.proxy_command_cmd}";' if self.proxy_command_cmd is not None else ';')

  return cmd, {}

 @classmethod
 def deserialize(cls, cmd: str, data: dict) -> Union['AutoSSHCmd', 'None']:
  match = re.match(
   '^autossh -M 0 -oStrictHostKeyChecking=no -fN (.*)@(.*) '
   r'-([RL]) (.*):([0-9]*):(.*):([0-9]*) -p ([0-9]*) -i "([^\"]*)";$',
   cmd
  )

  match = re.match(
   '^autossh -M 0 -oStrictHostKeyChecking=no -fN (.*)@(.*) '
   '-([RL]) (.*):([0-9]*):(.*):([0-9]*) -p ([0-9]*) -i "([^\"]*)" -oProxyCommand="(.*)";$',
   cmd
  ) if match is None else match

  if match is None:
   return None

  route = match.group(3)
  route = int(route == 'L')

  return cls(
   out_username=match.group(1),
   out_host=match.group(2),
   route=route,
   local_in_host=match.group(4) if route else match.group(6),
   local_in_port=match.group(5) if route else match.group(7),
   remote_in_host=match.group(6) if route else match.group(4),
   remote_in_port=match.group(7) if route else match.group(5),
   out_port=match.group(8),
   out_private_key_path=match.group(9),
   proxy_command_cmd=match.group(10) if len(match.groups()) > 9 else None,
   **data
  )


class PureCmd(BaseCmd):
 _required_fields = {'cmd', 'env'}

 def __init__(self, cmd, env=None):
  """
  `echo 'this is your custom cmd'`

  Аргументы берутся либо из node, либо из kwargs.
  Названия необходимых (только если node не задан) именнованных аргументов:

  :param cmd: команда
  :param env: переменные окружения для команды
  :type env: dict
  """

  env = {} if env is None else env
  self.cmd = cmd
  self._env = env

 @property
 def env(self) -> dict:
  return {**self._env}

 def serialize(self) -> Tuple[str, dict]:
  return f'{self.cmd}', {'env': self._env}

 @classmethod
 def deserialize(cls, cmd: str, data: dict) -> Union['PureCmd', 'None']:
  if 'env' not in data:
   return None

  return cls(cmd=cmd, **data)


class SSHRemoteCmd(BaseCmd):
 _required_fields = {'user', 'host', 'port', 'key_path', 'cmd', 'remote_env'}

 def __init__(self, node: Node = None, remote_cmd: BaseCmd = None, is_forwarded=True, **kwargs):
  # noinspection SpellCheckingInspection
  """
  `ssh user@host -p 6996 -i "/path/to/key" 'echo "this is remote cmd"';`

  Аргументы берутся либо из node и remote_cmd, либо из kwargs.
  Названия необходимых (только если node не задан) именнованных аргументов:

  :param is_forwarded: пробрешенно ли соединение до узла (необходим при использовании node)
  :param host: адрес удаленого узла
  :param port: порт ssh интерфейса удаленого узла
  :param username: имя пользователя удаленого узла
  :param key_path: путь до публичного ключа удаленого узла
  :param cmd: команда, которая должна исполниться на удаленном узле
  :param remote_env: переменные окружения для команды удаленного узла
  :type remote_env: dict
  """

  if node is None and self._required_fields - set(kwargs.keys()):
   raise TypeError(
    f'__init__() missing required arguments: edge or {", ".join(self._required_fields)}'
   )

  self.user = node.server.server_account.username if node is not None else kwargs['user']
  self.host = ('localhost' if is_forwarded else node.server.ssh_ip) \
   if node is not None else kwargs['host']
  self.port = int(
   (node.ssh_proc_port if is_forwarded else node.server.ssh_port)
   if node is not None else kwargs['port']
  )
  self.key_path = node.id_rsa.path if node is not None else kwargs['key_path']
  self.cmd = remote_cmd.serialize()[0] if remote_cmd is not None else kwargs['cmd']
  self.remote_env = remote_cmd.env if remote_cmd is not None else kwargs['remote_env']

 def _execute(self, ctx: Context, cmd: str, env: dict, **kwargs) -> Tuple[Result, bool]:
  with Connection(
    user=self.user,
    host=self.host,
    port=self.port,
    connect_kwargs={'key_filename': self.key_path},
    config=ctx.config,
    inline_ssh_env=True
    ) as conn:
   r = conn.run(self.cmd, env=env, **kwargs)
   return r, r.ok

 @property
 def env(self) -> dict:
  return {**self.remote_env}

 def serialize(self) -> Tuple[str, dict]:
  return f'ssh {self.user}@{self.host} -p {self.port} -i "{self.key_path}" \'{self.cmd}\';', \
     {'remote_env': self.remote_env}

 @classmethod
 def deserialize(cls, cmd: str, data: dict) -> Union['SSHRemoteCmd', 'None']:
  match = re.match(
   '^ssh (.*)@(.*) -p ([0-9]*) -i "(.*)" \'(.*)\';$',
   cmd
  )

  if match is None or 'remote_env' not in data:
   return None

  return cls(
   user=match.group(1),
   host=match.group(2),
   port=match.group(3),
   key_path=match.group(4),
   cmd=match.group(5),
   **data
  )


class KillProcCmd(BaseCmd):
 _required_fields = {'proc_filter'}

 def __init__(self, proc_filter: str):
  # noinspection SpellCheckingInspection
  """
  `kill -9 `ps auxe | grep "proc_filter" | grep -v grep | awk '{print $2}'` 2>/dev/null;`

  Пытается завершить все процессы, которые подходят по фильтру:

  :param proc_filter: фильтр процессов
  """

  self.proc_filter = proc_filter

 def _execute(self, ctx: Context, cmd: str, env: dict, **kwargs) -> Tuple[Result, bool]:
  r = ctx.run(cmd, env=env, **kwargs)
  is_ok = r.ok or r.return_code == 2

  return r, is_ok

 @property
 def env(self) -> dict:
  return {}

 def serialize(self) -> Tuple[str, dict]:
  # noinspection SpellCheckingInspection
  return 'kill -9 `ps auxe | grep "' + self.proc_filter + '" | grep -v grep | awk \'{print $2}\'` 2>/dev/null;', \
     {}

 @classmethod
 def deserialize(cls, cmd: str, data: dict) -> Union['KillProcCmd', 'None']:
  match = re.match(
   r'^kill -9 `ps auxe \| grep "(.*)" \| grep -v grep \| awk \'{print \$2}\'` 2>/dev/null;$',
   cmd
  )

  if match is None:
   return None

  return cls(
   proc_filter=data.get('proc_filter', match.group(1)),
  )


class ClearBuildCmd(BaseCmd):
 # noinspection SpellCheckingInspection
 _cmd = 'if [ -d external-worker ]; then cd external-worker/ && export PUID=`id -u` && export PGID=`id -g` && ' \
    'docker-compose down && docker rmi --force $APP_IMAGE_NAME && cd ~ && rm -rf external-worker; fi; ' \
    'mkdir -p ~/external-worker/keys && mkdir -p ~/external-worker/config/.ssh'

 _required_fields = {
  'openssh_container_external_port', 'app_image_name',
  'external_celery_queue_name', 'scraper_selenium_ide_templates_dir'
 }

 def __init__(self, anon_chain: Chain = None, **kwargs):
  """
  Очищает образы докера и директорию со сборкой

  Аргументы берутся либо из anon_chain, либо из kwargs.
  Названия необходимых (только если node не задан) именнованных аргументов:

  :param openssh_container_external_port: порт на котором весит openssh на удаленном узле
            (можно указать любое корректное значение)
  :param app_image_name: имя docker образа (sos_web-app/soi_web-app)
  :param external_celery_queue_name: имя очереди
  :param proxy_command_cmd: директория для шаблонов, по умолчанию SCRAPER_SELENIUM_IDE_TEMPLATES_DIR
  """

  if anon_chain is None and self._required_fields - set(kwargs.keys()) - {'scraper_selenium_ide_templates_dir'}:
   raise TypeError(
    f'__init__() missing required arguments: anon_chain or {", ".join(self._required_fields)}'
   )

  self.openssh_container_external_port = int(
   anon_chain.openssh_container_external_port
   if anon_chain is not None else kwargs['openssh_container_external_port']
  )
  self.app_image_name = anon_chain.app_image.name \
   if anon_chain is not None else kwargs['app_image_name']
  self.external_celery_queue_name = anon_chain.task_queue_name \
   if anon_chain is not None else kwargs['external_celery_queue_name']
  self.scraper_selenium_ide_templates_dir = kwargs.get(
   'scraper_selenium_ide_templates_dir',
   SCRAPER_SELENIUM_IDE_TEMPLATES_DIR
  )

 @property
 def env(self) -> dict:
  return {
   'DOCKER_OPENSSH_PORT': self.openssh_container_external_port,
   'APP_IMAGE_NAME': self.app_image_name,
   'EXTERNAL_CELERY_QUEUE_NAME': self.external_celery_queue_name,
   'SCRAPER_SELENIUM_IDE_TEMPLATES_DIR': self.scraper_selenium_ide_templates_dir
  }

 def serialize(self) -> Tuple[str, dict]:
  return self._cmd, {k: getattr(self, k) for k in self._required_fields}

 @classmethod
 def deserialize(cls, cmd: str, data: dict) -> Union['ClearBuildCmd', 'None']:
  if cmd != cls._cmd or cls._required_fields - set(data.keys()):
   return None

  return cls(**data)


# noinspection PyPep8Naming
class ScpCmd(BaseCmd):
 _required_fields = {
  'host', 'port', 'username', 'key_filepath', 'local_path',
  'remote_path', 'send', 'oStrictHostKeyChecking'
 }
 optional_fields = {'oStrictHostKeyChecking', 'local_path', 'remote_path', 'send'}

 def __init__(
   self, local_path: Union[str, Path], remote_path: Union[str, Path], send=True,
   node: Node = None, is_forwarded=True, oStrictHostKeyChecking='no', **kwargs
 ):
  """
  `scp -P 22 -i "key.pub" /path/to/local_file user@host:/path/to/remote;`

  :param local_path: путь до локального файла
  :param remote_path: путь до удаленого файла
  :param send: если Истина - отправляет файл, иначе - получает
  :param oStrictHostKeyChecking: параметр ssh (default is `no`, another values: `yes`, `ask`)

  Аргументы берутся либо из node, либо из kwargs.
  Названия необходимых (только если node не задан) именнованных аргументов:

  :param host: адрес узла
  :param port: порт ssh интерфейса
  :param username: имя пользователя
  :param key_filepath: путь до приватного ключа
  """

  if node is None and self._required_fields - set(kwargs.keys()) - self.optional_fields:
   raise TypeError(
    f'__init__() missing required arguments: node or {", ".join(self._required_fields)}'
   )

  if node is not None:
   self.host = 'localhost' if is_forwarded else node.server.ssh_ip
  else:
   self.host = kwargs['host']

  if node is not None:
   self.port = int(node.ssh_proc_port if is_forwarded else node.server.ssh_port)
  else:
   self.port = int(kwargs['port'])

  self.username = node.server.server_account.username if node is not None else kwargs['username']

  self.key_filepath = node.id_rsa.path if node is not None else kwargs['key_filepath']

  s = shlex.shlex(str(local_path), posix=True)
  s.whitespace_split = True
  local_path_fix = str.join('\\ ', list(s))
  self.local_path = f'{local_path_fix}'

  s = shlex.shlex(str(remote_path), posix=True)
  s.whitespace_split = True
  remote_path_fix = str.join('\\ ', list(s))

  self.remote_path = f'"{remote_path_fix}"'
  self.send = send
  self.oStrictHostKeyChecking = oStrictHostKeyChecking

 @property
 def env(self) -> dict:
  return {}

 def serialize(self) -> Tuple[str, dict]:
  scp_part = f"scp -oStrictHostKeyChecking={self.oStrictHostKeyChecking} -P {self.port} -i '{self.key_filepath}'"
  host_part = f'{self.username}@{self.host}:{self.remote_path}'
  cmd = scp_part + (f' {self.local_path} {host_part}' if self.send else f' {host_part} {self.local_path}')
  cmd += ';'
  return cmd, {}

 @classmethod
 def deserialize(cls, cmd: str, data: dict) -> Union['ScpCmd', 'None']:
  match, send = re.match(
   "^scp -oStrictHostKeyChecking=([a-z]*) -P ([0-9]*) -i '(.*)' (.*) (.*)@(.*):\"(.*)\";$", cmd
  ), True

  match, send = (
   re.match("^scp -oStrictHostKeyChecking=([a-z]*) -P ([0-9]*) -i '(.*)' (.*)@(.*):\|(.*)\" (.*);$", cmd),
   False
  ) if match is None else (match, send)

  if match is None:
   return None

  if send:
   return cls(
    oStrictHostKeyChecking=match.group(1),
    port=match.group(2),
    key_filepath=match.group(3),
    local_path=match.group(4),
    username=match.group(5),
    host=match.group(6),
    remote_path=match.group(7),
    send=send,
    **data
   )

  return cls(
   oStrictHostKeyChecking=match.group(1),
   port=match.group(2),
   key_filepath=match.group(3),
   username=match.group(4),
   host=match.group(5),
   remote_path=match.group(6),
   local_path=match.group(7),
   send=send,
   **data
  )


# noinspection SpellCheckingInspection
class SSGetFreePortCmd(BaseCmd):
 # https://unix.stackexchange.com/a/423052
 _required_fields = {'min_value', 'max_value', 'count', 'exclude'}

 def __init__(self, min_value=1024, max_value=65535, count=1, exclude: list = None):
  """
  `comm -23 <(seq 1024 65535 | sort) <(ss -Htan | awk '{print $4}' | cut -d':' -f2
  | sort -u) | shuf | grep -v "^[^0-9]$" | head -n 1;`

  :param min_value: минимальное значение порта
  :param max_value: максимальное значение порта
  :param count: необходимое количество
  :param exclude: исключить
  """

  self.min_value = int(min_value)
  self.max_value = int(max_value)
  self.exclude = exclude or []

  assert self.min_value >= 1 and self.max_value <= 65535, 'Wrong ports range'

  self.count = int(count)

 @property
 def env(self) -> dict:
  return {}

 def serialize(self) -> Tuple[str, dict]:
  exclude = '|'.join(f' grep -v "^{ep}$" ' for ep in self.exclude or ['[^0-9]'])

  cmd = f'comm -23 <(seq {self.min_value} {self.max_value} | sort) ' \
    '<(ss -Htan | awk \'{print $4}\' | cut -d\':\' -f2 | sort -u) | shuf |' \
    f'{exclude}| head -n {self.count};'
  return cmd, {}

 @classmethod
 def deserialize(cls, cmd: str, data: dict) -> Union['SSGetFreePortCmd', 'None']:
  # noinspection RegExpRedundantEscape
  match = re.match(
   r"^comm -23 <\(seq ([0-9]*) ([0-9]*) \| sort\) <\(ss -Htan \| awk '\{print \$4\}' "
   r"\| cut -d':' -f2 \| sort -u\) \| shuf "
   r'(\| grep -v "\^(.*)\$" \|)* head -n ([0-9]*);$',
   cmd
  )

  if match is None:
   return None

  groups = match.groups()
  exclude = groups[3:-1]

  # noinspection PyTypeChecker
  return cls(
   min_value=groups[0],
   max_value=groups[1],
   exclude=None if len(exclude) == 1 and exclude[0] == '[^0-9]' else exclude,
   count=groups[-1]
  )


class SSHKeyGenCmd(BaseCmd):
 _required_fields = {'file_path', 'key_bits', 'key_type'}

 def __init__(
   self, file_path: Union[Path, str],
   key_bits=settings.ANON_APP_SSH_KEYS_BITS,
   key_type=settings.ANON_APP_SSH_KEYS_TYPE
 ):
  """
  ssh-keygen -b 521 -t -f ecdsa -q -N "";

  :param file_path: минимальное значение порта
  :param key_bits: максимальное значение порта
  :param key_type: необходимое количество
  """

  self.file_path = str(file_path)
  self.key_bits = int(key_bits)
  self.key_type = key_type

 @property
 def env(self) -> dict:
  return {}

 def serialize(self) -> Tuple[str, dict]:
  cmd = f'ssh-keygen -b {self.key_bits} -t {self.key_type} -f {self.file_path} -q -N "";'
  return cmd, {}

 @classmethod
 def deserialize(cls, cmd: str, data: dict) -> Union['SSGetFreePortCmd', 'None']:
  # noinspection RegExpRedundantEscape
  match = re.match(
   '^ssh-keygen -b ([0-9]*) -t (.*) -f (.*) -q -N \"\";$',
   cmd
  )

  if match is None:
   return None

  # noinspection PyTypeChecker
  return cls(
   key_bits=match.group(1),
   key_type=match.group(2),
   file_path=match.group(3)
  )


class AnsiblePlaybookCmd(BaseCmd):
 # noinspection SpellCheckingInspection
 use_dir_flag_file_name = '.use-all-in-dir-soiplaybooks'
 # noinspection SpellCheckingInspection
 is_use_dir_flag_file_name = '.use-this-dir-soiplaybooks'
 meta_base_dir = Path('/tmp/ansible-data/meta')
 artifact_base_dir = Path('/tmp/ansible-data/artifact')

 _required_fields = {'user', 'password', 'host', 'port', 'ssh_key_path'}
 _hash_fields = {*_required_fields, 'tags', 'skip_tags', 'playbook_path', 'local_env'}

 def __init__(self, playbook_path: str, node: Node = None, is_forwarded=True, local_env: dict = None, **kwargs):
  # noinspection SpellCheckingInspection
  """
  `ansible-playbook --ask-become-pass -i /tmp/ansible-meta/projtmpdir/inventory/hosts /path/to/playbook.yml;`

  Создает директорию с мета-данными для исполнения задачи, название которое
  опредеяется как хэш от текущего экземляра класса. При необходимости избавиться
  от следов использования экземляра следует вызвать `ansibleplaybook_instance.kill().execute()`

  Аргументы берутся либо из node и remote_cmd, либо из kwargs.
  Названия необходимых (только если node не задан) именнованных аргументов:

  :param playbook_path: путь до используемого playbook (необходим всегда)
  :param local_env: переменные окружения, которые буду использованы локально при запуске (опционально)
  :param is_forwarded: пробрешенно ли соединение до узла (необходим при использовании node)
  :param host: адрес удаленого узла (locally - запускать на текущем хосте)
  :param port: порт ssh интерфейса удаленого узла
  :param user: имя пользователя удаленого узла
  :param password: пароль пользователя удаленого узла
  :param ssh_key_path: путь до публичного ключа удаленого узла
  :param skip_tags: теги, которые следует пропустить (список строк)
  :param tags: теги, которые следует выполнить (список строк)
  """

  if node is None and self._required_fields - set(kwargs.keys()):
   raise TypeError(
    f'__init__() missing required arguments: edge or {", ".join(self._required_fields)}'
   )

  self.user = node.server.server_account.username if node is not None else kwargs['user']
  self.password = node.server.server_account.password \
   if node is not None else kwargs['password']
  self.host = ('localhost' if is_forwarded else node.server.ssh_ip) \
   if node is not None else kwargs['host']
  self.port = int(
   (node.ssh_proc_port if is_forwarded else node.server.ssh_port)
   if node is not None else kwargs['port']
  )
  self.playbook_path = self.playbook_path_venv = self._playbook_path = playbook_path.__str__()
  self.ssh_key_path = node.id_rsa.path if node is not None else kwargs['ssh_key_path']
  self.local_env = local_env if local_env is not None else {}

  self.tags = kwargs.get('tags')
  self.tags = ','.join(self.tags) if self.tags else None
  self.skip_tags = kwargs.get('skip_tags')
  self.skip_tags = ','.join(self.skip_tags) if self.skip_tags else None

  if not os.path.exists(self.playbook_path):
   raise ValueError('playbook_path not exists')

  if not Path(self.playbook_path).is_absolute():
   raise ValueError('playbook_path must be absolute')

 @property
 def workdir(self):
  return self.meta_base_dir.joinpath(str(self.__hash__()).replace('-', '_'))

 @property
 def env(self) -> dict:
  return {**self.local_env}

 def copy_if_need(self):
  # noinspection SpellCheckingInspection
  """
  Копирует необходимык файлы плейбука в метадиректорию и использует только их,
  если рядом с указанным плейбуком лежит файл `.use-all-in-dir-soiplaybooks`

  return: True если нашелся файл .use-all-in-dir-soiplaybooks, иначе - False
  """

  playbook = Path(self.playbook_path_venv)
  if self.use_dir_flag_file_name not in os.listdir(playbook.parent):
   return False

  # такой костыль нужен, потому что dirs_exist_ok
  # в copytree появился лишь в 3.8+ версии
  for obj in playbook.parent.iterdir():
   if obj.name == self.use_dir_flag_file_name:
    continue

   if obj.is_dir():
    shutil.copytree(obj, self.workdir.joinpath(obj.name))
   else:
    shutil.copy(obj, self.workdir)

  # ставим метку, что данная метадиректория должна использоваться независимо от сторонних файлов
  with open(self.workdir.joinpath(self.is_use_dir_flag_file_name).__str__(), 'w') as _:
   pass

  self._playbook_path = self.workdir.joinpath(playbook.name).__str__()

  return True

 def _init_meta(self, artifact_dir=None):
  artifact_dir = self.artifact_base_dir.joinpath(
   str(self.__hash__()).replace('-', '_'), artifact_dir or time.time().__str__()
  )

  if not artifact_dir.exists():
   artifact_dir.mkdir(parents=True)

  if self.workdir.exists():
   if self.is_use_dir_flag_file_name in os.listdir(self.workdir):
    # проверяем есть ли метка в мета директории и если есть,
    # то используем плейбук который есть в ней
    self._playbook_path = self.workdir.joinpath(Path(self.playbook_path).name).__str__()
   self._runner = init_ansible_playbook_runner(
    playbook=self._playbook_path,
    private_data_dir=self.workdir.__str__(),
    artifact_dir=artifact_dir.__str__(),
    skip_tags=self.skip_tags, tags=self.tags
   )
   return

  self.workdir.mkdir(parents=True)

  # locally - запускать на текущем хосте

  ssh_key_data = ''
  if self.host != 'locally':
   with open(self.ssh_key_path) as ssh_key_file:
    ssh_key_data = ssh_key_file.read()

  self.copy_if_need()
  inventory = f'{self.host} ansible_user={self.user} ansible_port={self.port} ' \
     f'ansible_become_pass={self.password} ' \
     f'ansible_python_interpreter=/usr/bin/python3' \
   if self.host != 'locally' else 'localhost ansible_connection=local'

  # noinspection SpellCheckingInspection
  self._runner = init_ansible_playbook_runner(
   playbook=self._playbook_path,
   private_data_dir=self.workdir.__str__(),
   artifact_dir=artifact_dir.__str__(),
   ssh_key=ssh_key_data,
   envvars=self.runtime_env,
   inventory=inventory,
   # https://ansible-runner.readthedocs.io/en/latest/intro.html?highlight=become#env-passwords
   # cmdline='--ask-become-pass',
   # passwords={'^BECOME [pP]assword: ?$': self.password}
   # todo: report error https://gitlab.lan/filigree/soi/-/issues/99
  )

 def _execute(self, ctx: Context, cmd: str, env: dict, hide=True, warn=True) -> Tuple[Result, bool]:
  # todo: set logger, hide stdout, warn
  kill_cmd = self.kill() # удаляет старые файлы которые не исполняются

  if kill_cmd is not None:
   kill_cmd.execute() # убивает лишние процессы

  self._init_meta() # создает новый artifacts_dir
  self._runner.run()

  is_ok = self._runner.status == AnsibleRunnerStatus.SUCCESSFUL

  invoke_result = Result(
   stdout=self._runner.stdout.read() if is_ok else '',
   stderr=self._runner.stdout.read() if not is_ok else '',
   command=self.serialize()[0],
   env=env,
   exited=self._runner.rc
  )

  return invoke_result, is_ok

 def serialize(self) -> Tuple[str, dict]:
  return f'ansible-playbook ' \
     f'-i "{self.workdir.joinpath("inventory/hosts")}" ' \
     f'"{self._playbook_path}";', \
     dict(
      user=self.user,
      password=self.password,
      host=self.host,
      port=self.port,
      ssh_key_path=self.ssh_key_path,
      local_env=self.local_env,
      tags=self.tags,
      skip_tags=self.skip_tags
     )

 @classmethod
 def deserialize(cls, cmd: str, data: dict) -> Union['AnsiblePlaybookCmd', 'None']:
  match = re.match(
   f'^ansible-playbook -i "{cls.meta_base_dir}/.*/inventory/hosts" "(.*)";$',
   cmd
  )

  if match is None:
   return None

  # noinspection PyTypeChecker
  return cls(
   playbook_path=match.group(1),
   **data
  )

 def kill(self) -> Union['KillProcCmd', None]:
  """
  Удаляет директорию с мета-данными по этому экземляру AnsiblePlaybookCmd
  и возвращает экземпляр KillProcCmd для завершения работающих процессов
  """

  artifacts_dir = self.artifact_base_dir.joinpath(str(self.__hash__()).replace('-', '_'))
  kill_cmd = super(AnsiblePlaybookCmd, self).kill()

  if self.workdir.exists():
   logger.info(f'Remove {self.workdir} [AnsiblePlaybook][rm-meta-dir][{self.__hash__()}]')
   shutil.rmtree(self.workdir)

  if not artifacts_dir.exists():
   return kill_cmd

  logger.info(f'Remove artifacts [AnsiblePlaybook][rm-meta-dir][{self.__hash__()}]')

  for dir_ in artifacts_dir.iterdir():
   # dir is float timestamp string or `delete-me`
   if not dir_.is_dir():
    continue

   if dir_.name == 'delete-me':
    shutil.rmtree(dir_)
    continue

   for a_dir in dir_.iterdir():
    # a_dir is uuid4 string
    if not a_dir.is_dir() and not a_dir.joinpath('status').exists():
     continue

    status = a_dir.joinpath('status')
    status = status.read_text().strip() if status.exists() else AnsibleRunnerStatus.UNSTARTED

    if status in [
     AnsibleRunnerStatus.UNSTARTED,
     AnsibleRunnerStatus.STARTING,
     AnsibleRunnerStatus.RUNNING
    ]: # удаляем только если задача уже выполнена
     continue

    shutil.rmtree(a_dir)

   if not list(dir_.iterdir()):
    shutil.rmtree(dir_)

  if not list(artifacts_dir.iterdir()):
   shutil.rmtree(artifacts_dir)

  return kill_cmd


class InstallDockerPlaybookCmd(AnsiblePlaybookCmd):
 _plb_path = Path(DATA_PREFIX, 'anon_app/ansible-playbooks/install-docker.yml')

 def __init__(self, node: Node = None, is_forwarded=True, **kwargs):
  """
  Вернет экземпляр InstallDockerPlaybookCmd, который устанавливает докер
  на хост с debian, если он еще не был установлен

  :param node: хост
  :param is_forwarded: пробрешенно ли соединение до этого хоста или нет
  :param docker_username: имя удаленного пользователя, которого добавим в группу docker
  """

  if node is None:
   super(InstallDockerPlaybookCmd, self).__init__(
    playbook_path=self._plb_path, node=node,
    is_forwarded=is_forwarded, **kwargs
   )
   return

  local_env = {'REMOTE_USERNAME': node.server.server_account.username}

  super(InstallDockerPlaybookCmd, self).__init__(
   playbook_path=self._plb_path, node=node,
   is_forwarded=is_forwarded, local_env=local_env, **kwargs
  )


class PingPongPlaybookCmd(AnsiblePlaybookCmd):
 _plb_path = Path(DATA_PREFIX, 'anon_app/ansible-playbooks/ping.yml')

 def __init__(self, node: Node = None, is_forwarded=True, *args, **kwargs):
  """
  Вернет экземпляр PingPlaybookCmd, который проверяет работоспособность ансибла
  Если пришел ответ pong значит всё ок

  :param node: хост
  :param is_forwarded: пробрешенно ли соединение до этого хоста или нет
  """

  if node is None:
   super(PingPongPlaybookCmd, self).__init__(playbook_path=self._plb_path, *args, **kwargs)
   return

  super(PingPongPlaybookCmd, self).__init__(
   playbook_path=self._plb_path, node=node,
   is_forwarded=is_forwarded, **kwargs
  )


class InstallZipUnzipPlaybookCmd(AnsiblePlaybookCmd):
 _plb_path = Path(DATA_PREFIX, 'anon_app/ansible-playbooks/install-zip-unzip.yml')

 def __init__(self, node: Node = None, is_forwarded=True, **kwargs):
  """
  Вернет экземпляр InstallZipUnzipPlaybookCmd, который устанавливает zip и unzip
  на хост, если они еще не были установлен

  :param node: хост
  :param is_forwarded: пробрешенно ли соединение до этого хоста или нет
  """

  super(InstallZipUnzipPlaybookCmd, self).__init__(
   playbook_path=self._plb_path, node=node,
   is_forwarded=is_forwarded, **kwargs
  )


# noinspection SpellCheckingInspection
class AptInstallPlaybookCmd(AnsiblePlaybookCmd):
 _plb_path = Path(DATA_PREFIX, 'anon_app/ansible-playbooks/apt-install.yml')

 def __init__(self, packages: List[str], node: Node = None, is_forwarded=True, **kwargs):
  """
  Вернет экземпляр AptInstallPlaybookCmd, который устанавливает пакет
  на хост, если они еще не были установлен

  :param packages: список пакетов для установки
  :param node: хост
  :param is_forwarded: пробрешенно ли соединение до этого хоста или нет
  """

  local_env = kwargs.get('local_env', {})
  local_env['PACKAGES'] = str(packages)
  kwargs['local_env'] = local_env

  if node is None:
   super(AptInstallPlaybookCmd, self).__init__(playbook_path=self._plb_path, **kwargs)
   return

  super(AptInstallPlaybookCmd, self).__init__(
   playbook_path=self._plb_path, node=node,
   is_forwarded=is_forwarded, **kwargs
  )


class InstallProxychainsPlaybookCmd(AnsiblePlaybookCmd):
 playbook_path: str = Path(DATA_PREFIX, 'anon_app/ansible-playbooks/install-proxychains4.yml')

 def __init__(self, node: Node = None, is_forwarded=True, **kwargs):
  """
  Вернет экземпляр InstallProxychainsPlaybookCmd, который устанавливает и запускает proxychains

  :param node: хост
  :param is_forwarded: пробрешенно ли соединение до этого хоста или нет
  """

  local_env = kwargs.get('local_env', {})
  kwargs['local_env'] = local_env

  if node is None:
   super(InstallProxychainsPlaybookCmd, self).__init__(playbook_path=self.playbook_path, **kwargs)
   return

  super(InstallProxychainsPlaybookCmd, self).__init__(
   playbook_path=self.playbook_path, node=node,
   is_forwarded=is_forwarded, **kwargs
  )


class OpenVPNPlaybooksCmdBase(AnsiblePlaybookCmd):
 _plb_base_path = Path(DATA_PREFIX, 'anon_app/ansible-playbooks/openvpn/')

 class Actions(Enum):
  INSTALL_SERVER = 'install_server.yml'
  INSTALL_CLIENT = 'install_client.yml'
  ADD_CLIENT = 'add_client.yml'
  CONNECT = 'connect.yml'
  # TODO: REVOKE
  # TODO: UNINSTALL
  # TODO: RESTART

 # noinspection SpellCheckingInspection
 def __init__(
   self, ovpn_action=Actions.INSTALL_SERVER, ovpn_client='user',
   ovpn_server_network='10.228.0.0', ovpn_server_netmask='255.255.255.0',
   ovpn_port=1194, ovpn_sub_network='', ovpn_sub_netmask='', srv_ip=None,
   ovpn_conf: OpenVPNClient = None, is_forwarded=True, **kwargs
 ):
  """
  Ansible плейбуки для openvpn

  :param node: хост
  :param is_forwarded: пробрешенно ли соединение до этого хоста или нет
  :param action: выполняемое действие (по умолчанию установка с добавлением клиентов)
  """

  # todo: сделать возможность указывать сразу несколько ovpn_client и ovpn_sub_network

  if srv_ip is None and ovpn_conf is None:
   raise ValueError('Specify srv_ip or ovpn_conf.')

  local_env = {
   'OVPN_CLIENT_SOI': ovpn_conf.client if ovpn_conf is not None else ovpn_client,
   'OVPN_SRVNTWRK_SOI': ovpn_conf.node.ovpn_network if ovpn_conf is not None else ovpn_server_network,
   'OVPN_SRVNTMSK_SOI': ovpn_conf.node.ovpn_netmask if ovpn_conf is not None else ovpn_server_netmask,
   'OVPN_SRVADDR_SOI': ovpn_conf.node.server.ssh_ip if ovpn_conf is not None else srv_ip,
   'OVPN_PORT_SOI': ovpn_conf.node.ovpn_port if ovpn_conf is not None else ovpn_port,
   'OVPN_SUBNTWRK_SOI': (ovpn_conf.sub_network or '') if ovpn_conf is not None else ovpn_sub_network,
   'OVPN_SUBNTMSK_SOI': (ovpn_conf.sub_netmask or '') if ovpn_conf is not None else ovpn_sub_netmask,
   **kwargs.get('local_env', {})
  }

  kwargs['local_env'] = local_env
  ovpn_action = getattr(self.__class__, '_plb', ovpn_action)
  self._plb_path = self._plb_base_path.joinpath(ovpn_action.value)

  if ovpn_conf is None:
   super(OpenVPNPlaybooksCmdBase, self).__init__(
    playbook_path=self._plb_path, is_forwarded=is_forwarded, **kwargs
   )
   return

  super(OpenVPNPlaybooksCmdBase, self).__init__(
   playbook_path=self._plb_path, node=ovpn_conf.node,
   is_forwarded=is_forwarded, **kwargs
  )


class OpenVPNSrvInstallPlaybookCmd(OpenVPNPlaybooksCmdBase):
 _plb = OpenVPNPlaybooksCmdBase.Actions.INSTALL_SERVER


class OpenVPNClntInstallPlaybookCmd(OpenVPNPlaybooksCmdBase):
 _plb = OpenVPNPlaybooksCmdBase.Actions.INSTALL_CLIENT

 def __init__(self, node: Node = None, is_forwarded=True, **kwargs):
  # noinspection PyTypeChecker
  super(OpenVPNPlaybooksCmdBase, self).__init__(
   playbook_path=self._plb_base_path.joinpath(self._plb.value),
   node=node, is_forwarded=is_forwarded, **kwargs
  )


class OpenVPNAddClntPlaybookCmd(OpenVPNPlaybooksCmdBase):
 _plb = OpenVPNPlaybooksCmdBase.Actions.ADD_CLIENT


class OpenVPNConnectPlaybookCmd(OpenVPNPlaybooksCmdBase):
 _plb = OpenVPNPlaybooksCmdBase.Actions.CONNECT

 def __init__(
   self, config_path: str = None, ovpn_client: OpenVPNClient = None,
   node: Node = None, is_forwarded=True, **kwargs
 ):
  if config_path is None and ovpn_client is None:
   raise ValueError('Specify srv_ip or ovpn_conf.')

  kwargs['local_env'] = {
   'OVPN_CONFIG_PATH_SOI': ovpn_client.config.path if ovpn_client is not None else config_path,
   **kwargs.get('local_env', {})
  }

  # noinspection PyTypeChecker
  super(OpenVPNPlaybooksCmdBase, self).__init__(
   playbook_path=self._plb_base_path.joinpath(self._plb.value),
   node=node, is_forwarded=is_forwarded, **kwargs
  )


# noinspection SpellCheckingInspection
class ZabbixAgentManagePlaybookCmd(AnsiblePlaybookCmd):
 _plb_path = Path(DATA_PREFIX, 'anon_app/ansible-playbooks/zabbix-agent-manage/main.yml')

 # noinspection PyPep8Naming
 class actions(Enum):
  INSTALL = 'install-agent'
  RESTART = 'restart-service'
  CREATE_USER = 'create-user'

 def __init__(self, actions: List[actions] = None, node: Node = None, is_forwarded=True, **kwargs):
  """
  Вернет экземпляр ZabbixAgentManagePlaybookCmd, который устанавливает zabbix-agent
  на хост, если они еще не были установлен и/или запускает сервис

  :param node: хост
  :param is_forwarded: пробрешенно ли соединение до этого хоста или нет
  """

  tags = [a.value for a in actions] if actions is not None else None

  if node is None:
   super(ZabbixAgentManagePlaybookCmd, self).__init__(
    playbook_path=self._plb_path, node=node,
    is_forwarded=is_forwarded, tags=tags, **kwargs
   )

  local_env = {
   'ZBX_SERVER_PORT': node.forwarded_zabbix_port,
   'ZBX_HOSTNAME': node.server.ssh_ip
  }

  super(ZabbixAgentManagePlaybookCmd, self).__init__(
   playbook_path=self._plb_path, node=node,
   is_forwarded=is_forwarded, tags=tags, local_env=local_env,
   **kwargs
  )


class AddSwapfilePlaybookCmd(AnsiblePlaybookCmd):
 _plb_path = Path(DATA_PREFIX, 'anon_app/ansible-playbooks/add-swap.yml')

 # noinspection SpellCheckingInspection
 def __init__(
   self, swap_file_path: str = '/swapfile',
   swap_filesize_md: str = settings.ANON_APP_SWAP_FILE_SIZE_MB,
   node: Node = None, is_forwarded=True, **kwargs
 ):
  """
  Ansible плейбуки для создания swapfile (отработает только если нет свопа)

  :param swap_file_path: путь создания swapfile
  :param swap_filesize_md: размер создаваемого swapfile
  :param node: хост
  :param is_forwarded: пробрешенно ли соединение до этого хоста или нет
  :param action: выполняемое действие (по умолчанию установка с добавлением клиентов)
  """

  kwargs['local_env'] = {
   'SWAP_FILE_PATH': swap_file_path,
   'SWAP_FILE_SIZE_MB': swap_filesize_md,
   **kwargs.get('local_env', {})
  }

  super(AddSwapfilePlaybookCmd, self).__init__(
   playbook_path=self._plb_path, node=node,
   is_forwarded=is_forwarded, **kwargs
  )


class CheckProxy(SSHRemoteCmd):
 def __init__(self, proxy: Proxy, node: Node = None, is_forwarded=True, **kwargs):
  # noinspection PyTypeChecker
  check_cmd = {
   Proxy.ProtocolChoice.HTTP: f'curl -v --proxy {proxy.host_port} %s google.com 2>&1',
   Proxy.ProtocolChoice.HTTPS: f'curl -v --proxy {proxy.host_port} %s google.com 2>&1',
   Proxy.ProtocolChoice.Socks4: f'curl -v --socks4 {proxy.host_port} %s google.com 2>&1',
   Proxy.ProtocolChoice.Socks5: f'curl -v --socks5-hostname {proxy.host_port} %s google.com 2>&1',
  }[proxy.protocol]

  auth = f'-U "{proxy.username}:{proxy.password}" ' if proxy.username and proxy.password else ''
  check_cmd = check_cmd % auth
  check_cmd += ' | grep "^< HTTP"'
  check_cmd = PureCmd(check_cmd)

  super().__init__(**kwargs, remote_cmd=check_cmd, node=node, is_forwarded=is_forwarded)

 def _execute(self, *args, **kwargs) -> Tuple[Result, bool]:
  r, is_ok = super()._execute(*args, **kwargs)
  if not is_ok and r.return_code == 1 and r.stdout.strip():
   is_ok = True
  return r, is_ok


class GetHostCountry(SSHRemoteCmd):
 def __init__(self, node: Node = None, is_forwarded=True, **kwargs):
  # noinspection PyTypeChecker
  cmd = PureCmd(
   f'whois {node.server.ssh_ip} | grep -i "country:" | '
   f'head -n 1 | tr -d " " | cut -d ":" -f 2'
  )
  super().__init__(**kwargs, remote_cmd=cmd, node=node, is_forwarded=is_forwarded)