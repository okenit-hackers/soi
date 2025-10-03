import ipaddress
import logging
import os
import os.path
import random
import socket
import string
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Dict, List, Tuple, Union
from urllib.parse import urljoin

import requests
from django.core.files import File
from django.db import transaction
from invoke import Result
from lmgs_botservices.proxy import ProxyForSessions
from pyzabbix import ZabbixAPI
from retry import retry

from anon_app.conf import settings
from anon_app.exceptions import (AnonAppException, CmdError, OpenVPNFileDoesntExists, OpenVPNNeedRestart,
         TooManyOpenVPNFiles)
from anon_app.models import Chain, Edge, Node, OpenVPNClient, Proxy
from anon_app.tasks.cmd import (AddSwapfilePlaybookCmd, AnsiblePlaybookCmd, AptInstallPlaybookCmd, AutoSSHCmd, BaseCmd,
        CheckProxy, ClearBuildCmd, CmdChain, GetHostCountry, InstallDockerPlaybookCmd,
        InstallProxychainsPlaybookCmd, InstallZipUnzipPlaybookCmd, KillProcCmd,
        OpenVPNAddClntPlaybookCmd, OpenVPNClntInstallPlaybookCmd, OpenVPNConnectPlaybookCmd,
        OpenVPNSrvInstallPlaybookCmd, PureCmd, SSGetFreePortCmd, SSHCopyIdCmd, SSHKeyGenCmd,
        SSHRemoteCmd, ScpCmd, ZabbixAgentManagePlaybookCmd)
from notifications_app.models import Notification
from soi_app.settings import (
 DATA_PREFIX, EXTERNAL_SECOND_PG_HOST, EXTERNAL_SECOND_PG_PORT, LOGSTASH_EXTERNAL_CONF,
 LOGSTASH_EXTERNAL_FILEBEAT_CONF, LOGSTASH_EXTERNAL_HOST, LOGSTASH_EXTERNAL_PORT, NEEDED_TEMPLATES,
 SCRAPER_SELENIUM_IDE_TEMPLATES_DIR, SECOND_PG_HOST, SECOND_PG_PORT,
)

logger = logging.getLogger(__name__)
MICROSOCKS_PROTOCOL = 'socks5'
MICROSOCKS_IP = '172.17.0.1'
MICROSOCKS_PORT = '1080'


def prebuild_tunnel_edge(in_node: Node = Node, out_node: Node = None, set_only_if_is_null=False, exclude: List = None):
 if in_node is not None and (
   in_node.ssh_proc_port is None or not set_only_if_is_null
   and not CmdCtl.is_port_free(in_node.ssh_proc_port)
 ):
  in_node.ssh_proc_port = CmdCtl.get_random_ports(exclude=exclude)
  in_node.save(update_fields=['ssh_proc_port'])
 if out_node is not None and (
   out_node.ssh_proc_port is None or not set_only_if_is_null
   and not CmdCtl.is_port_free(out_node.ssh_proc_port)
 ):
  exclude_ = exclude or []
  out_node.ssh_proc_port = CmdCtl.get_random_ports(exclude=[*exclude_, in_node.ssh_proc_port])
  out_node.save(update_fields=['ssh_proc_port'])

 if isinstance(exclude, list):
  exclude.extend([in_node.ssh_proc_port, out_node.ssh_proc_port])


def prebuild_tunnel(chain: Chain, set_only_if_is_null=False):
 """
 Проверяет что необходимые порты для построения начального туннеля проставлены
 и свободны, иначе подбирает новый порт. Данный метод вызывается при каждом
 вызове `anon_app.tasks.utils.ChainCtl.build_tunnel`.

 :param chain: цепочка анонимизации
 :param set_only_if_is_null: задает новое значение только если текущее равно None
 """

 selected_ports = []

 for edge in chain.sorted_edges:
  prebuild_tunnel_edge(
   in_node=edge.in_node, out_node=edge.out_node,
   set_only_if_is_null=set_only_if_is_null,
   exclude=selected_ports
  )


def check_nodes_quantity(chain: Chain):
 """
 Проверяет состоит chain из одного узла или нескольких.

 :param chain: цепочка анонимизации
 :return srv_node: узел для создания oVPN сервера
   need_port_forwarding: необходимость проброса портов
 """

 in_node = chain.sorted_edges[0].in_node
 out_node = chain.sorted_edges[0].out_node

 # если узел один, используем его, если нет, то используем последний
 if in_node == out_node:
  srv_node = chain.get_validated_sorted_edges()[0].out_node
  need_port_forwarding = False
 else:
  srv_node = chain.exit_node
  need_port_forwarding = True

 return srv_node, need_port_forwarding


def build_openvpn_network(chain_ctl, chain: Chain, srv_node: Node, need_port_forwarding: bool):
 """
  Строит туннель и поднимает openVPN сервер на последнем узле.

  :param chain_ctl: цепочка анонимизации с методами для ее реализации
  :param chain: цепочка анонимизации
  :param srv_node: узел для создания oVPN сервера
  :param need_port_forwarding: необходимость проброса портов
 """

 # если True, то строим тунель с пробросом портов до последного узла
 if need_port_forwarding:
  logger.info(f'Start building openVPN config for chain of multiple nodes: {chain}')
  prebuild_tunnel(chain)
  chain_ctl.execute_tunnel_building()

 OpenVPNCtl.build_openvpn_conf(chain=chain, srv_node=srv_node, need_port_forwarding=need_port_forwarding)

 chain.status = Chain.StatusChoice.READY
 chain.save(update_fields=['status'])
 logger.info(f'OpenVPN chain was successfully created: {chain}')

 Notification.send_to_all(
  content=f'Цепочка {chain.title}, была успешно построена',
  log_level=Notification.LogLevelChoice.COLOR_SUCCESS.value
 )


# noinspection SpellCheckingInspection
def preup_openssh(chain: Chain, set_only_if_is_null=False):
 """
 Проверяет что необходимый порт на удаленном узле для контейнера openssh
 проставлен и свободны, иначе подбирает новый порт. Данный метод вызывается
 при каждом вызове `anon_app.tasks.utils.ChainCtl.up_openssh`.

 :param chain: цепочка анонимизации
 :param set_only_if_is_null: задает новое значение только если текущее равно None
 """

 exit_node = chain.exit_node

 if chain.openssh_container_external_port is None or not set_only_if_is_null and \
   not CmdCtl.is_port_free(chain.openssh_container_external_port, host=exit_node):
  # необходимо значение для завершения процессов https://gitlab.lan/filigree/soi/-/issues/95
  port = CmdCtl.get_random_ports(host=exit_node) if not set_only_if_is_null else random.randint(1024, 65535)
  assert port is not Node, 'Not found free ports on exit node for openssh'
  chain.openssh_container_external_port = port
  chain.save(update_fields=['openssh_container_external_port'])


# noinspection SpellCheckingInspection
def prefinish_up_tunnel(chain: Chain, set_only_if_is_null=False):
 """
 Проверяет что необходимый порт на удаленном узле для контейнера openssh
 проставлен и свободны, иначе подбирает новый порт. Данный метод вызывается
 при каждом вызове `anon_app.tasks.utils.ChainCtl.finish_up_tunnel`.

 :param chain: цепочка анонимизации
 :param set_only_if_is_null: задает новое значение только если текущее равно None
 """

 exit_node = chain.exit_node

 if chain.openssh_container_internal_port is None or not set_only_if_is_null \
   and not CmdCtl.is_port_free(chain.openssh_container_internal_port, host=exit_node):
  # необходимо значение для завершения процессов https://gitlab.lan/filigree/soi/-/issues/95
  port = CmdCtl.get_random_ports(host=exit_node) if not set_only_if_is_null else random.randint(1024, 65535)
  assert port is not Node, 'Not found free ports on exit node for openssh'
  chain.openssh_container_internal_port = port
  chain.save(update_fields=['openssh_container_internal_port'])


# noinspection SpellCheckingInspection
def preforward_zabbix(chain: Chain, set_only_if_is_null=False):
 """
 Проверяет что необходимый для zabbix порт проставлен и свободен,
 иначе подбирает новый порт.

 :param chain: цепочка анонимизации
 :param set_only_if_is_null: задает новое значение только если текущее равно None
 """

 selected_ports = []

 for i, node in enumerate(chain.sorted_nodes):

  if node.forwarded_zabbix_port is None or not set_only_if_is_null \
    and not CmdCtl.is_port_free(node.forwarded_zabbix_port):
   port = CmdCtl.get_random_ports(host=node, exclude=selected_ports, is_forwarded=i != 0) \
    if not set_only_if_is_null else random.randint(1024, 65535) # необходимо значение для завершения
   # процессов https://gitlab.lan/filigree/soi/-/issues/95
   assert port is not Node, f'Not found free ports for zabbix agent [{chain}]'
   selected_ports.append(port)
   node.forwarded_zabbix_port = port
   node.save(update_fields=['forwarded_zabbix_port'])
  selected_ports.append(node.forwarded_zabbix_port)


# noinspection SpellCheckingInspection
def postforward_zabbix(chain: Chain):
 """
 Добавляет информацию о забикс агенте на сервер забикса, если это необходимо

 :param chain: цепочка анонимизации
 """

 zapi = ZabbixAPI(
  url=settings.ANON_APP_ZABBIX_SERVER_URL,
  user=settings.ANON_APP_ZABBIX_SERVER_USER,
  password=settings.ANON_APP_ZABBIX_SERVER_PASSWORD
 )

 for node in chain.sorted_nodes:
  # todo: возможно стоит хранить id'шники забикса в бд
  host = zapi.host.get(filter={'host': node.server.ssh_ip})

  if host:
   continue

  chain_group = zapi.hostgroup.get(filter={"name": chain.title})
  if not chain_group:
   chain_group_id = zapi.hostgroup.create(name=chain.title)['groupids'][0]
  else:
   chain_group_id = chain_group[0]['groupid']

  chains_group = zapi.hostgroup.get(filter={"name": settings.ANON_APP_ZABBIX_SERVER_CHAIN_GROUP})
  if not chains_group:
   chains_group_id = zapi.hostgroup.create(
    name=settings.ANON_APP_ZABBIX_SERVER_CHAIN_GROUP
   )['groupids'][0]
  else:
   chains_group_id = chains_group[0]['groupid']

  need_template_ids = []
  all_templates = zapi.template.get(output=['templateid', 'name'])
  for template in all_templates:
   if template['name'] in NEEDED_TEMPLATES:
    need_template_ids.append(template['templateid'])

  zapi.host.create(
   host=node.server.ssh_ip,
   groups=[{"groupid": chain_group_id}, {"groupid": chains_group_id}],
   templates=[{"templateid": template_id} for template_id in need_template_ids]
  )


class OpenVPNCtl:
 def __init__(
   self, edge: Edge = None, out_node: Union[Node, str] = 'localhost',
   in_node: Node = None, is_forwarded=True, is_private_conf=False,
   sub_network: str = None, sub_netmask: str = None, edges: list = None,
 ):
  assert edge is not None or None not in (out_node, in_node), 'WTF'
  assert out_node is None or isinstance(out_node, Node) or out_node == 'localhost', 'WTF'

  self._edge = edge
  self._out_node = edge.out_node if edge is not None else out_node
  self._in_node = edge.in_node if edge is not None else in_node
  self._edges = edges if edges else [self._in_node]
  self._is_forwarded = not is_private_conf and is_forwarded
  self._is_private_conf = is_private_conf
  self._autossh2kill = None
  self._ovpn_client_conf = None
  self._cmd_workdir = None
  self._sub_network = sub_network
  self._sub_netmask = sub_netmask

  self.results = {}

  prebuild_tunnel_edge(in_node=self._in_node, set_only_if_is_null=True)

 def _get_access_to_srv(self):
  # получаем доступ до хоста, куда должны поставить OpenVPN сервер
  self._autossh2kill = AutoSSHCmd(self._edge, is_forwarded=self._is_forwarded)
  cmd_chain = SSHCopyIdCmd(self._out_node, is_forwarded=self._is_forwarded) \
     | self._autossh2kill | SSHCopyIdCmd(self._in_node, is_forwarded=not self._is_private_conf)
  self.results.update(cmd_chain.run())

 def _specify_network(self):
  # задаем подсеть OpenVPN
  while not CmdCtl.is_network_free(
    self._in_node.ovpn_network_full.compressed,
    self._in_node, is_forwarded=not self._is_private_conf
  ):
   octets = self._in_node.ovpn_network.split('.')
   octets[2] = str(int(octets[2]) + 1)
   self._in_node.ovpn_network = '.'.join(octets)

  self._in_node.save(update_fields=['ovpn_network'])

 def _specify_srv_port(self):
  # задаем порт сервера OpenVPN
  if not CmdCtl.is_port_free(self._in_node.ovpn_port, self._in_node, is_forwarded=not self._is_private_conf):
   self._in_node.ovpn_port = CmdCtl.get_random_ports(host=self._in_node)
   self._in_node.save(update_fields=['ovpn_port'])

 def _install_playbook_deps(self):
  AptInstallPlaybookCmd(node=self._in_node, packages=['lsb-release']).execute()
  AptInstallPlaybookCmd(node=self._out_node, packages=['lsb-release'], is_forwarded=self._is_forwarded).execute()

 def _create_config(self):
  # задаем имя клиента OpenVPN
  from faker import Faker
  fake = Faker()
  client = fake.user_name()
  self._ovpn_client_conf = OpenVPNClient.objects.create(
   node=self._in_node, client=client, sub_network=self._sub_network,
   is_private=self._is_private_conf, sub_netmask=self._sub_netmask
  ) if self._ovpn_client_conf is None else self._ovpn_client_conf

  while not self._ovpn_client_conf.client or not CmdCtl.is_ovpn_client_free(
    client=self._ovpn_client_conf.client,
    host=self._in_node,
    is_forwarded=self._is_forwarded
  ):
   self._ovpn_client_conf.client = fake.user_name()

  self._ovpn_client_conf.save(update_fields=['client'])
  return self._ovpn_client_conf

 def _install_srv(self):
  # ставим OpenVPN сервер, запускаем, добавляем указанного юзера, выгружаем конфиг
  srv_install_cmd = OpenVPNSrvInstallPlaybookCmd(
   ovpn_conf=self._ovpn_client_conf, is_forwarded=not self._is_private_conf
  )
  srv_install_result = srv_install_cmd.execute()
  self._cmd_workdir = srv_install_cmd.workdir
  self.results.update({srv_install_cmd: srv_install_result})

 def _save_conf(self):
  # записываем сгенеренный конфиг клиента в бд
  conf_dir = self._cmd_workdir.joinpath(
   settings.ANON_APP_OPENVPN_FETCH_CONFIG_DIR,
   self._ovpn_client_conf.client
  )

  if not conf_dir.exists() or not list(conf_dir.iterdir()):
   raise CmdError(f'Conf dir didnt create: {self._cmd_workdir}')

  conf_abs_path = list(conf_dir.iterdir())[0]

  with open(conf_abs_path) as file:
   self._ovpn_client_conf.config.save(
    f'{self._ovpn_client_conf.node.server.ssh_ip}-{self._ovpn_client_conf.client}.ovpn',
    File(file)
   )

 def _install_client(self):
  # ставим OpenVPN клиент
  # kwargs = dict(
  #  node=self._out_node,
  #  is_forwarded=self._is_forwarded
  # ) if not self._is_private_conf else dict(
  #  user='root', password='todo', host='locally', port=22, ssh_key_path='todo'
  # )

  install_clnt_cmd = OpenVPNClntInstallPlaybookCmd(node=self._out_node, is_forwarded=self._is_forwarded)
  install_clnt_result = install_clnt_cmd.execute()
  self.results.update({install_clnt_cmd: install_clnt_result})

 def _connect(self):
  # подключаем клиента к серверу
  kwargs = dict(
   ovpn_client=self._ovpn_client_conf,
   node=self._out_node,
   is_forwarded=self._is_forwarded
  ) if not self._is_private_conf else dict(
   ovpn_client=self._ovpn_client_conf,
   user='root', password='todo', host='locally',
   port=22, ssh_key_path='todo'
  )

  connect_cmd = OpenVPNConnectPlaybookCmd(**kwargs)
  connect_result = connect_cmd.execute()
  self.results.update({connect_cmd: connect_result})

 def _gather_facts(self):
  # получаем оставшуюся информацию
  ovpn_server_ip = CmdCtl.get_node_ip_in_network(
   network=self._in_node.ovpn_network_full.compressed,
   host=self._in_node,
   is_forwarded=not self._is_private_conf
  )
  if not ovpn_server_ip:
   raise CmdError(f'not found srv ip on {self._in_node}')
  self._in_node.ovpn_srv_ip = ovpn_server_ip
  self._in_node.save(update_fields=['ovpn_srv_ip'])

  try:
   ovpn_client_ip = CmdCtl.get_node_ip_in_network(
    network=self._in_node.ovpn_network_full.compressed,
    host=self._out_node, is_forwarded=self._is_forwarded
   )
  except CmdError as exc:
   vpn_device_error = 'Error: either'
   if vpn_device_error in str(exc):
    logger.info('start to delete device')
    delete_ovpns(self._edges)
    logger.error('Found garbage device need to restart')
    raise OpenVPNNeedRestart()
   else:
    raise

  if not ovpn_client_ip:
   raise CmdError(f'not found client ip on {self._in_node}')
  self._ovpn_client_conf.client_ip = ovpn_client_ip
  self._ovpn_client_conf.save(update_fields=['client_ip'])

 def _kill_tmp_ssh_connection(self):
  result = CmdChain(self._autossh2kill.kill()).run()
  self.results.update(result)

 def _ssh_over_ovpn(self):
  result = CmdChain(AutoSSHCmd(
   self._edge, remote_in_host=self._in_node.ovpn_srv_ip,
   is_forwarded=self._is_forwarded
  )).run()
  self.results.update(result)

 # noinspection DuplicatedCode
 @classmethod
 def build(
   cls, edge: Edge, edges: list, sub_network: str = None, sub_netmask: str = None, **kwargs
 ) -> Dict[BaseCmd, Result]:

  is_forwarded = kwargs.get('is_forwarded')
  ovpn_ctl = cls(
   edge, edges=edges, is_forwarded=is_forwarded,
   sub_network=sub_network, sub_netmask=sub_netmask
  )
  ovpn_ctl._get_access_to_srv()
  ovpn_ctl._specify_network()
  ovpn_ctl._specify_srv_port()
  ovpn_ctl._create_config()
  ovpn_ctl._install_playbook_deps()
  ovpn_ctl._install_srv()
  ovpn_ctl._save_conf()
  ovpn_ctl._install_client()
  ovpn_ctl._connect()
  ovpn_ctl._gather_facts()
  ovpn_ctl._kill_tmp_ssh_connection()
  ovpn_ctl._ssh_over_ovpn()
  return ovpn_ctl.results

 # noinspection DuplicatedCode
 @classmethod
 @transaction.atomic
 @retry(OpenVPNNeedRestart, tries=2, delay=120)
 def build4private_network(cls, srv_node: Node):
  default_network = CmdCtl.get_default_gateway_network()
  network = ipaddress.IPv4Network(settings.ANON_APP_OPENVPN_NETWORK2SHARE) \
   if settings.ANON_APP_OPENVPN_NETWORK2SHARE is not None else default_network
  logger.info(f'OVPN: {network} selected as default route on server [{srv_node}]')

  cmd_chain = CmdChain(SSHCopyIdCmd(srv_node, is_forwarded=False))
  cmd_chain.run()

  ovpn_ctl = cls(
   out_node='localhost', in_node=srv_node,
   is_private_conf=True, sub_network=network.network_address.compressed,
   sub_netmask=network.netmask.compressed
  )
  ovpn_ctl._specify_network()
  ovpn_ctl._specify_srv_port()
  ovpn_ctl._create_config()
  ovpn_ctl._install_srv()
  ovpn_ctl._save_conf()
  ovpn_ctl._connect()
  CmdCtl.set_iptables_masquerade(srv_node.ovpn_network_full)
  ovpn_ctl._gather_facts()

  return ovpn_ctl.results

 @classmethod
 def reconnect_private_network(cls, srv_node: Node):
  private_clients = OpenVPNClient.objects.filter(node__id=srv_node.id, is_private=True)
  private_clients_count = private_clients.count()
  if not private_clients_count:
   raise OpenVPNFileDoesntExists(f'Node "{srv_node}" has no any private client configs.')
  if private_clients_count > 1:
   raise TooManyOpenVPNFiles(
    f'Node "{srv_node}" has more than one private config file. Please leave only one.')

  default_network = CmdCtl.get_default_gateway_network()
  network = ipaddress.IPv4Network(settings.ANON_APP_OPENVPN_NETWORK2SHARE) \
   if settings.ANON_APP_OPENVPN_NETWORK2SHARE is not None else default_network

  ovpn_ctl = cls(
   out_node='localhost', in_node=srv_node,
   is_private_conf=True, sub_network=network.network_address.compressed,
   sub_netmask=network.netmask.compressed
  )
  ovpn_ctl._ovpn_client_conf = private_clients.first()
  ovpn_ctl._connect()
  CmdCtl.set_iptables_masquerade(srv_node.ovpn_network_full)
  ovpn_ctl._gather_facts()

  return ovpn_ctl.results

 @classmethod
 def prebuild_openvpn_conf_one_node(cls, chain: Chain):
  prebuild_tunnel(chain, set_only_if_is_null=True)
  preup_openssh(chain, set_only_if_is_null=True)
  prefinish_up_tunnel(chain, set_only_if_is_null=True)

 @classmethod
 @transaction.atomic
 def build_openvpn_conf(cls, chain: Chain, srv_node: Node, need_port_forwarding: bool):
  """
  Создает клиента openvpn в докер контейнере на удаленном узле и скачивает его в контейнер сервера управления.
  Построение происходит как для одного узла, так и для множества узлов.

  :param chain: объект цепочки анонимизации из БД
  :param srv_node: узел для создания oVPN сервера
  :param need_port_forwarding: необходимость проброса портов
  """

  try:
   from sos_app.settings import MEDIA_ROOT as media
  except ModuleNotFoundError:
   from soi_app.settings import MEDIA_ROOT as media

  ovpn_conf_name = f'{chain.title}.ovpn'.replace('chain', 'ovpn-conf')
  remote_file_path = f'/root/{ovpn_conf_name}'
  media_ovpn_dir = Path('open_vpn_configs/', ovpn_conf_name)
  absolute_ovpn_path = Path(media, media_ovpn_dir)

  # создаем папку на хосте
  PureCmd(f'mkdir -p {absolute_ovpn_path.parent}; ').execute()

  cmd_chain = CmdChain()

  if not need_port_forwarding:
   # в случае с несколькими узлами, ssh-copy-id уже отработал
   cmd_chain |= SSHCopyIdCmd(srv_node, is_forwarded=need_port_forwarding)

  cmd_chain |= AptInstallPlaybookCmd(
   node=srv_node, packages=['curl', 'lsb-release'], is_forwarded=need_port_forwarding,
  ) | InstallDockerPlaybookCmd(node=srv_node, is_forwarded=need_port_forwarding)

  start_openvpn_container = PureCmd(
   'docker run -d --restart on-failure --cap-add=NET_ADMIN -it -p 1194:1194/udp -p 80:8080/tcp -e HOST_ADDR=$(curl -s https://api.ipify.org) alekslitvinenk/openvpn; '
  )
  create_ovpn_client = PureCmd(
   f'curl http://{srv_node.server.ssh_ip}/ > {ovpn_conf_name}; '
  )
  cmd_chain |= SSHRemoteCmd(node=srv_node, remote_cmd=start_openvpn_container, is_forwarded=need_port_forwarding)
  cmd_chain |= SSHRemoteCmd(node=srv_node, remote_cmd=create_ovpn_client, is_forwarded=need_port_forwarding)
  cmd_chain |= ScpCmd(
   node=srv_node,
   local_path=str(absolute_ovpn_path),
   remote_path=remote_file_path,
   key_filepath=chain.openssh_container_id_rsa.path,
   send=False, is_forwarded=need_port_forwarding
  )
  cmd_chain.run()

  if absolute_ovpn_path.exists():
   logger.info(f'Client openVPN config file was created for chain from one node: {chain}')
   chain.openvpn_config.name = str(media_ovpn_dir)
   chain.save(update_fields=['openvpn_config'])

   cmd_chain = CmdChain()
   delete_ovpn_client = PureCmd(f'rm {ovpn_conf_name}; ')
   cmd_chain |= SSHRemoteCmd(node=srv_node, remote_cmd=delete_ovpn_client, is_forwarded=need_port_forwarding)
   cmd_chain.run()
  else:
   raise OpenVPNFileDoesntExists(f'OpenVPN config file was not created for chain from one node: {chain}')

 @classmethod
 def kill_all_containers(cls, chain: Chain, srv_node: Node, need_port_forwarding: bool):
  """
  Удаляет все запущенные докер контейнеры.

  :param chain: объект цепочки анонимизации из БД
  :param srv_node: узел для создания oVPN сервера
  :param need_port_forwarding: необходимость проброса портов
  """

  logger.info(f'[{chain}]: start killing all docker containers')

  cmd_chain = CmdChain()
  kill_containers = PureCmd('docker rm -f $(docker ps -a -q); ')
  cmd_chain |= SSHRemoteCmd(node=srv_node, remote_cmd=kill_containers, is_forwarded=need_port_forwarding)
  cmd_chain.run()

  logger.info(f'[{chain}]: successfully killed all docker containers')

 @classmethod
 def add_client(
   cls, srv_node: Node = None, is_forwarded=True,
   sub_network: Union[ipaddress.IPv4Network, ipaddress.IPv6Network] = None,
   ovpn_conf: OpenVPNClient = None
 ):
  if not (srv_node is None) ^ (ovpn_conf is None):
   raise ValueError(f'Specify only srv_node or only ovpn_conf')

  # noinspection PyUnresolvedReferences
  ovpn_ctl = cls(
   out_node='localhost', in_node=srv_node or ovpn_conf.node, is_forwarded=is_forwarded,
   sub_network=sub_network.network_address.compressed if sub_network is not None else None,
   sub_netmask=sub_network.netmask.compressed if sub_network is not None else None
  )

  ovpn_ctl._ovpn_client_conf = ovpn_conf
  ovpn_ctl._create_config()

  add_client_cmd = OpenVPNAddClntPlaybookCmd(ovpn_conf=ovpn_ctl._ovpn_client_conf, is_forwarded=is_forwarded)
  # noinspection PyUnresolvedReferences
  cmd_chain = SSHCopyIdCmd(node=ovpn_ctl._ovpn_client_conf.node, is_forwarded=is_forwarded) | add_client_cmd

  cmd_chain.run()
  ovpn_ctl._cmd_workdir = add_client_cmd.workdir
  ovpn_ctl._save_conf()

  return ovpn_ctl._ovpn_client_conf, {**ovpn_ctl.results, **cmd_chain.results}


# noinspection SpellCheckingInspection
def delete_ovpns(edges):
 """
 Удаляет впны со всех узлов

 :return: None
 """

 delete_ovpn_cmd = PureCmd('apt-get purge openvpn* -y & rm -rf /etc/openvpn')
 reboot_cmd = PureCmd('reboot')

 delete_ovpn_chain = CmdChain()
 for index, edge in enumerate(edges, start=1):
  is_forwarded = index != 1
  delete_ovpn_chain |= SSHRemoteCmd(edge.out_node, remote_cmd=delete_ovpn_cmd, is_forwarded=is_forwarded)
 # удаляем впны на всех нодах
 delete_ovpn_chain.run(raise_exc=False, is_need_exit=False)

 reboot_chain = CmdChain()
 last_edge_index = len(edges)
 reversed_edges = reversed(edges)
 # ребутаем в обратной последовательности
 for index, edge in enumerate(reversed_edges, start=1):
  is_forwarded = last_edge_index
  reboot_chain |= SSHRemoteCmd(edge.out_node, remote_cmd=reboot_cmd, is_forwarded=is_forwarded)

 # ребутаем все ноды
 reboot_chain.run(raise_exc=False, is_need_exit=False)


class ChainCtl:
 def __init__(self, chain: Chain):
  """
  :param chain: цепока анонимизации
  """

  self.anon_chain = chain

 def execute_chain_building(self) -> Dict[BaseCmd, Result]:
  """Выполнение создания цепочки"""

  result = {}

  prebuild_tunnel(self.anon_chain, set_only_if_is_null=True)
  preup_openssh(self.anon_chain, set_only_if_is_null=True)
  prefinish_up_tunnel(self.anon_chain, set_only_if_is_null=True)
  preforward_zabbix(self.anon_chain, set_only_if_is_null=True)

  self.kill_connection_proc().run(raise_exc=False)

  prebuild_tunnel(self.anon_chain)
  result.update(self.execute_tunnel_building())

  self.execute_update_geo()

  cmd_chain = self.clear_exit_node() | self.install_exit_node_dependencies() | self.upload_chain_files()
  result.update(cmd_chain.run())

  preup_openssh(self.anon_chain)
  cmd_chain = self.up_openssh()
  result.update(cmd_chain.run())

  prefinish_up_tunnel(self.anon_chain)
  cmd_chain = self.finish_up_tunnel() | self.forward_ports() | self.up_celery_worker()
  result.update(cmd_chain.run())

  # если не получилось накатить куда то забикс то оставляем всё как есть
  # noinspection PyBroadException
  try:
   zabbix_result = self.execute_zabbix2nodes()
   result.update(zabbix_result)
   postforward_zabbix(self.anon_chain)
  except Exception as e:
   logger.warning(f'Can\'t execute zabbix2nodes: {e}', exc_info=True)

  return result

 def port_forwarding_for_priority_celery_queue(self) -> Dict[BaseCmd, Result]:
  """Проброс портов для priority_celery_queue."""
  result = {}

  result.update(self.execute_tunnel_building_for_priority_queue())
  result.update(self.finish_up_tunnel().run())

  return result

 @staticmethod
 def build_proxies_chain(srv_node, need_port_forwarding):
  """Build proxychains4 on a remote server with ansible playbook.

  :param srv_node: remote server for building proxychains4.
  :param need_port_forwarding: Need for port forwarding.
  """
  logger.info(f'Start to build proxychains4 and microsocks on {srv_node}')
  cmd_chain = CmdChain()
  cmd_chain |= InstallProxychainsPlaybookCmd(node=srv_node, is_forwarded=need_port_forwarding)
  cmd_chain.run()
  logger.info(f'Successfully build proxychains4 and microsocks on {srv_node}')

 def generate_proxychains4_config(self, proxies, srv_node, is_forwarded):
  """Create proxychains4.conf with selected proxies and send it to a remote server.

  :param proxies: proxies for generating proxychains4.conf file.
  :param srv_node: remote server for building proxychains4.
  :param is_forwarded: Need for port forwarding.
  """
  logger.info(f'Start to generate proxychains4 configuration on {self.anon_chain.title}')
  try:
   from sos_app.settings import MEDIA_ROOT as media
  except ModuleNotFoundError:
   from soi_app.settings import MEDIA_ROOT as media

  cmd_chain = CmdChain()
  base_config = [
   'strict_chain', 'proxy_dns', 'remote_dns_subnet 224',
   'remote_dns_subnet 224', 'tcp_connect_time_out 8000', '[ProxyList]',
  ]
  proxychains_config = base_config + proxies
  config_name = 'proxychains4.conf'

  with tempfile.NamedTemporaryFile('w+t') as tmp_config:
   tmp_config.writelines(f'{line}\n' for line in proxychains_config)
   tmp_config.flush()
   cmd_chain |= ScpCmd(
    node=srv_node, is_forwarded=is_forwarded, key_filepath=self.anon_chain.openssh_container_id_rsa.path,
    local_path=tmp_config.name, remote_path=os.path.join('/etc', config_name),
   )
   cmd_chain.run()
   logger.info(f'{config_name} successfully generated and sent on {self.anon_chain.title}')

 def zabbix2nodes(self) -> CmdChain:
  nodes = self.anon_chain.sorted_nodes
  cmd_chain = CmdChain()
  for i, node in enumerate(nodes):
   cmd_chain |= CmdCtl.zabbix2node(node, i != 0)
  return cmd_chain

 @retry(Exception, delay=5, tries=3)
 def execute_zabbix2nodes(self) -> Dict[BaseCmd, Result]:
  preforward_zabbix(self.anon_chain)
  cmd_chain = self.zabbix2nodes()
  return cmd_chain.run()

 @retry(OpenVPNNeedRestart, tries=2, delay=120)
 def execute_tunnel_building(self) -> Dict[BaseCmd, Result]:
  """
  Генерирует цепочку команд для построения
  цепочки анонимизации и исполняет их

  :return: вернутся результаты команд
  """

  # noinspection SpellCheckingInspection
  edges = self.anon_chain.sorted_edges
  cmd_chain, results = CmdChain(), {}

  restart_tor_container = PureCmd(
   '[ `docker ps | grep "shpaker/torsocks" | cut -c -12` ] && '
   'docker stop `docker ps | grep "shpaker/torsocks" | cut -c -12`; '
   'docker run -d -p 9051:9050 --restart always shpaker/torsocks;'
  )
  generate_proxy_command = lambda edge_: SSHRemoteCmd(edge_.out_node, remote_cmd=PureCmd(
   f'connect -4 -S localhost:9051 {edge_.in_node.server.ssh_ip} '
   f'{edge_.in_node.server.ssh_port}'
  ))

  if len(edges) == 1 and edges[0].protocol == Edge.ProtocolChoice.SSH_VIA_TOR:
   edge = edges[0]
   return ChainCtl.create_tor_connection_one_edge(
    cmd_chain, results,
    edge, restart_tor_container
   )

  for i, edge in enumerate(edges):
   is_forwarded = i != 0
   cmd_chain |= SSHCopyIdCmd(edge.out_node, is_forwarded=is_forwarded)

   if edge.protocol == Edge.ProtocolChoice.SSH:
    cmd_chain |= AutoSSHCmd(edge, is_forwarded=is_forwarded)

   elif edge.protocol == Edge.ProtocolChoice.SSH_VIA_TOR:
    cmd_chain |= AptInstallPlaybookCmd(node=edge.out_node, packages=['curl', 'lsb-release']) \
        | InstallDockerPlaybookCmd(node=edge.out_node) \
        | AptInstallPlaybookCmd(node=edge.out_node, packages=['connect-proxy'])

    cmd_chain |= SSHRemoteCmd(
     edge.out_node, remote_cmd=restart_tor_container
    )

    proxy_command = generate_proxy_command(edge)

    cmd_chain |= SSHCopyIdCmd(
     edge.in_node, is_forwarded=False, proxy_command=proxy_command
    ) | AutoSSHCmd(
     out_host=edge.in_node.server.ssh_ip,
     out_port=edge.in_node.server.ssh_port,
     out_username=edge.in_node.server.server_account.username,
     out_private_key_path=edge.in_node.id_rsa.path,
     remote_in_host='localhost',
     remote_in_port=edge.in_node.server.ssh_port,
     local_in_port=edge.in_node.ssh_proc_port,
     proxy_command=proxy_command
    )
   elif edge.protocol == Edge.ProtocolChoice.VPN:
    results.update(cmd_chain.run())
    kwargs = dict(edge=edge, is_forwarded=is_forwarded)

    # TODO: wtfovpn
    # if self.anon_chain.is_for_private_network:
    #  network = CmdCtl.get_default_gateway_network()
    #  kwargs.update({
    #   'sub_network': network.network_address.compressed,
    #   'sub_netmask': network.netmask.compressed
    #  })

    ovpn_results = OpenVPNCtl.build(edges=self.anon_chain.sorted_edges, **kwargs)

    results.update(ovpn_results)
    cmd_chain = CmdChain()
   else:
    raise Exception(f'wtf: unknown edge.protocol: {edge.protocol}')

  if edges[-1].protocol != Edge.ProtocolChoice.SSH_VIA_TOR:
   cmd_chain |= SSHCopyIdCmd(edges[-1].in_node)

  results.update(cmd_chain.run())
  return results

 @retry(OpenVPNNeedRestart, tries=2, delay=120)
 def execute_tunnel_building_for_priority_queue(self) -> Dict[BaseCmd, Result]:
  edges = self.anon_chain.sorted_edges
  cmd_chain, results = CmdChain(), {}

  def generate_proxy_command(edge_: Edge, is_forwarded_: bool):
   pure_cmd = PureCmd(
    f'connect -4 -S localhost:9051 {edge_.in_node.server.ssh_ip} {edge_.in_node.server.ssh_port}'
   )
   return SSHRemoteCmd(edge_.out_node, is_forwarded=is_forwarded_, remote_cmd=pure_cmd)

  for i, edge in enumerate(edges):
   is_forwarded = i != 0
   cmd_chain |= SSHCopyIdCmd(edge.out_node, is_forwarded=is_forwarded)

   if edge.protocol == Edge.ProtocolChoice.SSH:
    cmd_chain |= AutoSSHCmd(edge, is_forwarded=is_forwarded)
   elif edge.protocol == Edge.ProtocolChoice.SSH_VIA_TOR:
    proxy_command = generate_proxy_command(edge_=edge, is_forwarded_=is_forwarded)
    cmd_chain |= AutoSSHCmd(
     out_host=edge.in_node.server.ssh_ip,
     out_port=edge.in_node.server.ssh_port,
     out_username=edge.in_node.server.server_account.username,
     out_private_key_path=edge.in_node.id_rsa.path,
     remote_in_host='localhost',
     remote_in_port=edge.in_node.server.ssh_port,
     local_in_port=edge.in_node.ssh_proc_port,
     proxy_command=proxy_command,
    )
   elif edge.protocol == Edge.ProtocolChoice.VPN:
    results.update(cmd_chain.run())
    openvpn_ctl = OpenVPNCtl(edge=edge, edges=self.anon_chain.sorted_edges, is_forwarded=is_forwarded)
    openvpn_ctl._ssh_over_ovpn()
    results.update(openvpn_ctl.results)
    cmd_chain = CmdChain()
   else:
    raise Exception(f'wtf: unknown edge.protocol: {edge.protocol}')

  results.update(cmd_chain.run())
  return results

 def execute_update_geo(self):
  for i, node in enumerate(self.anon_chain.sorted_nodes):
   is_forwarded = i != 0

   country = CmdCtl.get_host_country(node=node, is_forwarded=is_forwarded)
   if not country:
    continue

   node.server.geo = country
   node.server.save(update_fields=['geo'])

 @staticmethod
 def create_tor_connection_one_edge(cmd_chain: CmdChain, results: dict, edge: Edge, restart_tor_container: PureCmd):
  """
  Создает соединение TOR между узлами, если связь между узлами цепи равна 1
  :param cmd_chain: cmd команды для построения цепочки анонимизации
  :param results: результаты исполнения cmd команд
  :param edge: связь между узлами цепи
  :param restart_tor_container: bash команда для перезапуска TOR контейнера

  :return: вернутся результаты команд
  """

  generate_proxy_command = lambda edge_: SSHRemoteCmd(edge_.out_node, is_forwarded=False, remote_cmd=PureCmd(
   f'connect -4 -S localhost:9051 {edge_.in_node.server.ssh_ip} '
   f'{edge_.in_node.server.ssh_port}'
  ))

  cmd_chain |= SSHCopyIdCmd(edge.out_node, is_forwarded=False)

  cmd_chain |= AptInstallPlaybookCmd(node=edge.out_node, packages=['curl', 'lsb-release'], is_forwarded=False) \
      | InstallDockerPlaybookCmd(node=edge.out_node, is_forwarded=False) \
      | AptInstallPlaybookCmd(node=edge.out_node, packages=['connect-proxy'], is_forwarded=False)

  cmd_chain |= SSHRemoteCmd(
   edge.out_node, remote_cmd=restart_tor_container, is_forwarded=False
  )

  proxy_command = generate_proxy_command(edge)

  cmd_chain |= SSHCopyIdCmd(
   edge.in_node, is_forwarded=False, proxy_command=proxy_command
  ) | AutoSSHCmd(
   out_host=edge.in_node.server.ssh_ip,
   out_port=edge.in_node.server.ssh_port,
   out_username=edge.in_node.server.server_account.username,
   out_private_key_path=edge.in_node.id_rsa.path,
   remote_in_host='localhost',
   remote_in_port=edge.in_node.server.ssh_port,
   local_in_port=edge.in_node.ssh_proc_port,
   proxy_command=proxy_command
  )
  results.update(cmd_chain.run())
  return results

 def forward_ports(self) -> CmdChain:
  """
  Генерирует цепочку команд для проброса портов redis, postgres, logstash, etc.

  :return: вернется цепочка команд
  """

  return self.forward_redis() | self.forward_rabbit() | \
     self.forward_external_logstash() | self.forward_pg() | \
     self.forward_external_logstash_filebeat() | self.forward_avagen()

 def kill_connection_proc(self) -> CmdChain:
  """
  Генерирует цепочку команд для завершения процессов
  ssh туннеля цепочки анонимизации

  :return: вернется цепочка команд
  """

  # build_chain_kill = self.build_tunnel().kill()

  edges = self.anon_chain.sorted_edges
  cmd_chain = CmdChain(KillProcCmd(edges[0].out_node.server.ssh_ip))
  if len(edges) == 1 and edges[0].out_node == edges[0].in_node:
   # skip kill process if one node
   edges = []

  for i, edge in enumerate(edges):
   is_forwarded = i != 0

   cmd_chain |= KillProcCmd(edge.in_node.server.ssh_ip)

   if edge.in_node.ovpn_srv_ip is not None:
    cmd_chain |= AutoSSHCmd(
     edge, remote_in_host=edge.in_node.ovpn_srv_ip,
     is_forwarded=is_forwarded
    ).kill()

  return (
    cmd_chain | self.finish_up_tunnel().kill()
    | self.forward_ports().kill() | self.zabbix2nodes().kill()
  )

 def execute_rebuild_connection(self) -> Dict[BaseCmd, Result]:
  """
  Генерирует цепочку команд для перепостроения коннектов
  (туннель + проброс портов) цепочки анонимизации

  :return: вернется цепочка команд
  """

  # todo: проверить что необходимые контейнеры подняты и поднять их если это не так
  # fixme: MUSTHAVE НАПИСАТЬ ПО ЛЮДСКИ

  kill_result = self.kill_connection_proc().run()
  tunell_building_result = self.execute_tunnel_building()
  smth_finish_result = (self.finish_up_tunnel() | self.forward_ports()).run()
  zabbix_result = self.execute_zabbix2nodes()

  return {**kill_result, **tunell_building_result, **smth_finish_result, **zabbix_result}

 def clear_exit_node(self) -> CmdChain:
  clear_cmd = ClearBuildCmd(self.anon_chain)
  exit_node = self.anon_chain.exit_node
  remote_clear_cmd = SSHRemoteCmd(exit_node, clear_cmd)

  return CmdChain(remote_clear_cmd)

 def upload_chain_files(self) -> CmdChain:
  exit_node = self.anon_chain.exit_node

  cmd_chain = CmdChain()

  # загружаем файлы на выходную ноду
  files2upload = (
   (self.anon_chain.app_image.image.path, '~/external-worker/image.zip'),
   (self.anon_chain.app_image.docker_compose.path, '~/external-worker/docker-compose.yml'),
   (self.anon_chain.app_image.env.path, '~/external-worker/celery.env'),
   (self.anon_chain.openssh_container_id_rsa_pub.path, '~/external-worker/keys'),
   (self.anon_chain.app_image.browser_profiles.path, '~/external-worker/browser_profiles.zip'),
   (self.anon_chain.app_image.filebeat_config.path, '~/external-worker/filebeat.yml')
  )

  for src, dest in files2upload:
   cmd_chain |= ScpCmd(node=exit_node, is_forwarded=True, local_path=src, remote_path=dest)

  unzip_image_cmd = PureCmd(
   'cd ~/external-worker/ && yes | unzip image.zip '
   '&& export PUID=`id -u` && export PGID=`id -g` && ls -1 *.tar | xargs --no-run-if-empty -L 1 docker load -i',
   env={
    'DOCKER_OPENSSH_PORT': self.anon_chain.openssh_container_external_port,
    'APP_IMAGE_NAME': self.anon_chain.app_image.name,
    'EXTERNAL_CELERY_QUEUE_NAME': self.anon_chain.task_queue_name,
    'SCRAPER_SELENIUM_IDE_TEMPLATES_DIR': SCRAPER_SELENIUM_IDE_TEMPLATES_DIR
   }
  )
  cmd_chain |= SSHRemoteCmd(exit_node, unzip_image_cmd) # extract docker image and load it

  update_keys_cmd = PureCmd(
   'cd ~/external-worker/ && cat config/.ssh/authorized_keys keys/*.pub '
   '2>/dev/null 1>config/.ssh/authorized_keys'
  )
  # write pub keys to authorized_keys of remote openssh container
  cmd_chain |= SSHRemoteCmd(exit_node, update_keys_cmd)

  unzip_profiles_cmd = PureCmd('cd ~/external-worker/ && unzip -o browser_profiles.zip -d browser_profiles')
  cmd_chain |= SSHRemoteCmd(exit_node, unzip_profiles_cmd) # unzip browser's profiles

  return cmd_chain

 def up_openssh(self) -> CmdChain:
  cmd_chain = CmdChain(PureCmd(
   f"ssh-keygen -R '[localhost]:{self.anon_chain.openssh_container_internal_port}';"
  )) # remove key of docker host from known_hosts

  env = {
   'DOCKER_OPENSSH_PORT': self.anon_chain.openssh_container_external_port,
   'APP_IMAGE_NAME': self.anon_chain.app_image.name,
   'EXTERNAL_CELERY_QUEUE_NAME': self.anon_chain.task_queue_name,
   'SCRAPER_SELENIUM_IDE_TEMPLATES_DIR': SCRAPER_SELENIUM_IDE_TEMPLATES_DIR
  }
  docker_compose_up_cmd = PureCmd(
   f"cd ~/external-worker/ && export PUID=`id -u` && "
   f"export PGID=`id -g` && docker-compose up -d openssh && "
   f"docker-compose exec -d openssh chown "
   f"docker_user:root '{SCRAPER_SELENIUM_IDE_TEMPLATES_DIR}'",
   env=env
  ) # Start openssh service

  cmd_chain |= SSHRemoteCmd(
   self.anon_chain.exit_node,
   docker_compose_up_cmd
  )

  return cmd_chain

 def finish_up_tunnel(self) -> CmdChain:
  exit_node = self.anon_chain.exit_node
  cmd_chain = CmdChain()

  cmd_chain |= AutoSSHCmd(
   out_host='localhost',
   out_port=exit_node.ssh_proc_port,
   out_username=exit_node.server.server_account.username,
   out_private_key_path=exit_node.id_rsa.path,
   remote_in_host='localhost',
   remote_in_port=self.anon_chain.openssh_container_external_port,
   local_in_port=self.anon_chain.openssh_container_internal_port
  ) # extend ssh tunnel to remote openssh container

  return cmd_chain

 def forward_redis(self) -> CmdChain:
  cmd_chain = CmdChain()

  cmd_chain |= AutoSSHCmd(
   out_host='localhost',
   out_port=self.anon_chain.openssh_container_internal_port,
   out_username='docker_user', # todo: расхардкодить
   out_private_key_path=self.anon_chain.openssh_container_id_rsa.path,
   route=0, remote_in_host='localhost',
   remote_in_port=settings.ANON_APP_EXTERNAL_REDIS_PORT,
   local_in_host=settings.REDIS_HOST,
   local_in_port=settings.REDIS_PORT
  ) # reverse forwarding of local redis process port

  return cmd_chain

 def forward_rabbit(self) -> CmdChain:
  cmd_chain = CmdChain()

  cmd_chain |= AutoSSHCmd(
   out_host='localhost',
   out_port=self.anon_chain.openssh_container_internal_port,
   out_username='docker_user', # todo: расхардкодить
   out_private_key_path=self.anon_chain.openssh_container_id_rsa.path,
   route=0, remote_in_host='localhost',
   remote_in_port=settings.ANON_APP_EXTERNAL_RABBITMQ_PORT,
   local_in_host=settings.RABBITMQ_HOST,
   local_in_port=settings.RABBITMQ_PORT
  ) # reverse forwarding of local rabbit process port

  return cmd_chain

 def forward_external_logstash(self) -> CmdChain:
  cmd_chain = CmdChain()

  cmd_chain |= AutoSSHCmd(
   out_host='localhost',
   out_port=self.anon_chain.openssh_container_internal_port,
   out_username='docker_user', # todo: расхардкодить
   out_private_key_path=self.anon_chain.openssh_container_id_rsa.path,
   route=0, remote_in_host=LOGSTASH_EXTERNAL_CONF['host'],
   remote_in_port=LOGSTASH_EXTERNAL_CONF['port'],
   local_in_host=LOGSTASH_EXTERNAL_HOST,
   local_in_port=LOGSTASH_EXTERNAL_PORT
  ) # reverse forwarding of local logstash process port

  return cmd_chain

 def forward_external_logstash_filebeat(self) -> CmdChain:
  cmd_chain = CmdChain()

  cmd_chain |= AutoSSHCmd(
   out_host='localhost',
   out_port=self.anon_chain.openssh_container_internal_port,
   out_username='docker_user', # todo: расхардкодить
   out_private_key_path=self.anon_chain.openssh_container_id_rsa.path,
   route=0, remote_in_host=LOGSTASH_EXTERNAL_FILEBEAT_CONF['host'],
   remote_in_port=LOGSTASH_EXTERNAL_FILEBEAT_CONF['port'],
   local_in_host=LOGSTASH_EXTERNAL_HOST,
   local_in_port=LOGSTASH_EXTERNAL_FILEBEAT_CONF['port'] # todo check port config
  ) # reverse forwarding of local logstash filebeat process port

  return cmd_chain

 def forward_pg(self) -> CmdChain:
  cmd_chain = CmdChain()

  cmd_chain |= AutoSSHCmd(
   out_host='localhost',
   out_port=self.anon_chain.openssh_container_internal_port,
   out_username='docker_user', # todo: расхардкодить
   out_private_key_path=self.anon_chain.openssh_container_id_rsa.path,
   route=0, remote_in_host=EXTERNAL_SECOND_PG_HOST,
   remote_in_port=EXTERNAL_SECOND_PG_PORT,
   local_in_host=SECOND_PG_HOST,
   local_in_port=SECOND_PG_PORT
  ) # reverse forwarding of local pg process port

  return cmd_chain

 def forward_avagen(self) -> CmdChain:
  cmd_chain = CmdChain()

  cmd_chain |= AutoSSHCmd(
   out_host='localhost',
   out_port=self.anon_chain.openssh_container_internal_port,
   out_username='docker_user', # todo: расхардкодить
   out_private_key_path=self.anon_chain.openssh_container_id_rsa.path,
   route=0, remote_in_host=settings.ANON_APP_EXTERNAL_AVAGEN_HOST,
   remote_in_port=settings.ANON_APP_EXTERNAL_AVAGEN_PORT,
   local_in_host=settings.ANON_APP_AVAGEN_HOST,
   local_in_port=settings.ANON_APP_AVAGEN_PORT
  ) # reverse forwarding of local avagen service port

  return cmd_chain

 # def forward_zabbix_agent(self) -> CmdChain:
 #  exit_node = self.anon_chain.get_validated_sorted_edges()[-1].in_node
 #  cmd_chain = CmdChain()
 #
 #  cmd_chain |= AutoSSHCmd(
 #   out_host='localhost',
 #   out_port=exit_node.ssh_proc_port,
 #   out_username=exit_node.server.server_account.username,
 #   out_private_key_path=exit_node.id_rsa,
 #   remote_in_host=settings.ANON_APP_EXTERNAL_ZABBIX_AGENT_HOST,
 #   remote_in_port=settings.ANON_APP_EXTERNAL_ZABBIX_AGENT_PORT,
 #   local_in_host='0.0.0.0', # zabbix-stub-server -> celery-internal:10050
 #   local_in_port=self.anon_chain.zabbix_agent_internal_port or settings.ANON_APP_ZABBIX_AGENT_PORT
 #  ) # reverse forwarding of local zabbix process port
 #
 #  return cmd_chain

 def up_celery_worker(self) -> CmdChain:
  if self.anon_chain.concurrency == 0:
   total_cpu_cmd = PureCmd('nproc --all')
   command_result = SSHRemoteCmd(self.anon_chain.exit_node, total_cpu_cmd).execute()
   total_cpu = int(command_result.stdout.strip())
  else:
   total_cpu = self.anon_chain.concurrency
  p_concurrency = round(total_cpu * 0.2)
  concurrency = total_cpu - p_concurrency

  env = {
   'CONCURRENCY': concurrency,
   'PRIORITY_CONCURRENCY': p_concurrency,
   'DOCKER_OPENSSH_PORT': self.anon_chain.openssh_container_external_port,
   'APP_IMAGE_NAME': self.anon_chain.app_image.name,
   'EXTERNAL_CELERY_QUEUE_NAME': self.anon_chain.task_queue_name,
   'PRIORITY_EXTERNAL_CELERY_QUEUE_NAME': f'priority_{self.anon_chain.task_queue_name}',
   'SCRAPER_SELENIUM_IDE_TEMPLATES_DIR': SCRAPER_SELENIUM_IDE_TEMPLATES_DIR
  }
  username = self.anon_chain.exit_node.server.server_account.username
  password = self.anon_chain.exit_node.server.server_account.password

  docker_compose_up_cmd = PureCmd(
   'cd external-worker/ && export PUID=`id -u` && export PGID=`id -g` && docker-compose up -d celery && '
   'docker-compose up -d filebeat && docker-compose up -d priority_celery', # todo move run filebeat to other command
   env=env
  ) # Start openssh service

  cmd_chain = CmdChain()
  cmd_chain |= SSHRemoteCmd(
   self.anon_chain.exit_node,
   docker_compose_up_cmd
  )

  return cmd_chain

 def install_exit_node_dependencies(self):
  exit_node = self.anon_chain.exit_node
  return AptInstallPlaybookCmd(node=exit_node, packages=['lsb-release']) \
     | AddSwapfilePlaybookCmd(node=exit_node) | InstallDockerPlaybookCmd(node=exit_node) \
     | InstallZipUnzipPlaybookCmd(node=exit_node) \
     | AptInstallPlaybookCmd(node=exit_node, packages=['curl'])


# noinspection SpellCheckingInspection
class CmdCtl:
 @classmethod
 def zabbix2node(cls, node, is_forwarded=True) -> CmdChain:
  return cls.install_zabbix_agentd(
   node, is_forwarded
  ) | cls.forward_zabbix(
   node, is_forwarded
  ) | cls.restart_zabbix_agentd(
   node, is_forwarded
  )

 @classmethod
 def forward_zabbix(cls, node, is_forwarded=True) -> CmdChain:
  return CmdChain(AutoSSHCmd(
   out_host='localhost' if is_forwarded else node.server.ssh_ip,
   out_port=node.ssh_proc_port if is_forwarded else node.server.ssh_port,
   out_username=node.server.server_account.username,
   out_private_key_path=node.id_rsa,
   route=0, remote_in_host=settings.ANON_APP_EXTERNAL_ZABBIX_HOST,
   remote_in_port=node.forwarded_zabbix_port,
   local_in_host=settings.ANON_APP_ZABBIX_HOST,
   local_in_port=settings.ANON_APP_ZABBIX_PORT
  )) # reverse forwarding of local zabbix process port

 @classmethod
 def restart_zabbix_agentd(cls, node, is_forwarded=True) -> CmdChain:
  return CmdChain(ZabbixAgentManagePlaybookCmd(
   node=node, is_forwarded=is_forwarded,
   actions=[ZabbixAgentManagePlaybookCmd.actions.RESTART]
  ))

 @classmethod
 def install_zabbix_agentd(cls, node=None, is_forwarded=True) -> CmdChain:
  return CmdChain(ZabbixAgentManagePlaybookCmd(
   node=node, is_forwarded=is_forwarded,
   actions=[ZabbixAgentManagePlaybookCmd.actions.INSTALL]
  ))

 @classmethod
 def is_network_free(
   cls, network_with_mask: str,
   host: Union[Node, str] = 'localhost',
   is_forwarded=True
 ) -> bool:
  """
  Проверяет свободена ли подсеть на хосте

  :param network_with_mask: проверяемая подсеть (ex: 10.1.0.0/24)
  :param host: либо localhost (проверяем на локальном хосте), либо экземпляр
     anon_app.models.Node (проверяем на удаленном хосте)
  :param is_forwarded: имеет значение только когда `isinstance(host, Node)`
  """
  cmd = PureCmd(
   f'[[ $(ip route 2>/dev/null | grep "^{network_with_mask}") ]] && echo 0 || echo 1'
  )

  if host == 'localhost':
   return cmd.execute().stdout.strip() == '1'

  remote = SSHRemoteCmd(host, cmd, is_forwarded=is_forwarded)
  return remote.execute().stdout.strip() == '1'

 @classmethod
 def is_ovpn_client_free(
   cls, client: str,
   host: Union[Node, str] = 'localhost',
   is_forwarded=True
 ) -> bool:
  """
  Проверяет свободена ли подсеть на хосте

  :param client: проверяемый юзернейм
  :param host: либо localhost (проверяем на локальном хосте), либо экземпляр
     anon_app.models.Node (проверяем на удаленном хосте)
  :param is_forwarded: имеет значение только когда `isinstance(host, Node)`
  """
  cmd = PureCmd(
   f'[[ $(ls -1 {settings.ANON_APP_OPENVPN_SRV_DIR} '
   f'| grep "{client}-.*\.ovpn") ]] && echo 0 || echo 1'
  )

  if host == 'localhost':
   return cmd.execute().stdout.strip() == '1'

  remote = SSHRemoteCmd(host, cmd, is_forwarded=is_forwarded)
  return remote.execute().stdout.strip() == '1'

 # noinspection SpellCheckingInspection
 @classmethod
 def get_default_gateway_network(
   cls, host: Union[Node, str] = 'localhost',
   is_forwarded=True
 ) -> Union[ipaddress.IPv4Network, ipaddress.IPv6Network]:
  networks_cmd = PureCmd(
   'iface=`ip route list | grep default | awk \'{print $5}\'` '
   '&& ip route list | grep -v default | grep $iface | grep -Po "^[\d./:a-f]*"'
  )
  default_gateway_cmd = PureCmd(
   'ip route list | grep default | awk \'{print $3}\''
  )

  if host != 'localhost':
   networks_cmd = SSHRemoteCmd(host, networks_cmd, is_forwarded=is_forwarded)
   default_gateway_cmd = SSHRemoteCmd(host, default_gateway_cmd, is_forwarded=is_forwarded)

  networks = networks_cmd.execute().stdout.strip().splitlines()
  default_gateway = default_gateway_cmd.execute().stdout.strip()

  if not default_gateway:
   raise ValueError(f'Not found dafault gateway: {default_gateway_cmd}')

  if not networks:
   raise ValueError(f'Not found networks: {networks_cmd}')

  if '\n' in default_gateway:
   raise ValueError(f'Very much default gateways: {networks_cmd}')

  networks = [ipaddress.ip_network(n) for n in networks]
  default_gateway = ipaddress.ip_address(default_gateway)

  for network in networks:
   if default_gateway in list(network.hosts()):
    return network

  raise ValueError(
   f'Not found networj of default gateway: {default_gateway_cmd} | {networks_cmd}'
  )

 @classmethod
 def get_node_ip_in_network(
   cls, network: str, host: Union[Node, str] = 'localhost',
   ipv6=False, is_forwarded=True
 ) -> str:
  """
  Получает адрес узла в подсети

  :param network: подсеть с маской (ex: 10.0.0.0/24)
  :param host: нода на которой находится сервер
  :param is_forwarded: имеет значение только когда `isinstance(host, Node)`
  :param ipv6: по умолчанию ложно, то есть пытаемся получить ipv4 адрес
  """

  regex = 'inet6 \K[\da-f:]+' if ipv6 else 'inet \K[\d.]+'
  cmd = PureCmd(
   f"interface=`ip route list {network} "
   "| awk '{print $5}'` && "
   f"ip a show dev $interface | grep -Po '{regex}'"
  )

  if host == 'localhost':
   return cmd.execute().stdout.strip()

  remote = SSHRemoteCmd(host, cmd, is_forwarded=is_forwarded)
  return remote.execute().stdout.strip()

 @classmethod
 def is_port_free(cls, port, host: Union[Node, str] = 'localhost', is_forwarded=True) -> bool:
  """
  Проверяет свободен ли тот или иной порт

  :param port: проверяемый порт
  :param host: либо localhost (проверяем на локальном хосте), либо экземпляр
     anon_app.models.Node (проверяем на удаленном хосте)
  :param is_forwarded: имеет значение только когда `isinstance(host, Node)`
  """

  if host == 'localhost':
   with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
    result = s.connect_ex((host, port)) != 0
   return result

  if isinstance(host, Node):
   nmap = PureCmd(
    '[[ $(ss -Htan | awk \'{print $4}\' | ' +
    f'grep ":{port}$") ]] && echo 1 || echo 0'
   )
   remote_nmap = SSHRemoteCmd(host, nmap, is_forwarded=is_forwarded)
   return remote_nmap.execute().stdout.strip() == '0'

  raise TypeError(f'Expected Union[Node, str] but got {type(host)}')

 @classmethod
 def get_random_ports(
   cls, min_port: int = None, max_port: int = None, count=1,
   host: Union[Node, str] = 'localhost', is_forwarded=True,
   exclude: list = None
 ) -> Union[List[int], int, None]:
  """
  Находит свободные порты по заданным ограничениям

  :param min_port: минимальный порт
  :param max_port: максимальный порт
  :param count: количество
  :param host: хост где нужно искать (либо `'localhost'` либо объект типа anon_app.models.Node)
  :param is_forwarded: нужен когда `isinstance(host, Node)`
  :param exclude: исключить порты
  :return: список свободных портов или одно значение, если count == 1,
    или None если при этом свободный порт не найден
  """

  get_random_port_cmd = SSGetFreePortCmd(
   min_value=1024 if min_port is None else min_port,
   max_value=65535 if max_port is None else max_port,
   count=count, exclude=exclude
  )

  if host == 'localhost' and count == 1 and min_port is None and max_port is None:
   # если хотим один рандомный порт, более быстрый метод
   with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
    s.bind(('', 0))
    s.listen(1)
    port = s.getsockname()[1]
   return port

  if host == 'localhost':
   # если хотим рандомный локальный порт
   r = get_random_port_cmd.execute()
   ports = [int(p.strip()) for p in r.stdout.strip().split('\n') if p.strip().isdecimal()]
   if count == 1:
    return ports[0] if ports else None
   return ports

  if isinstance(host, Node):
   # если хотим рандомный порт на удаленном хосте
   remote_cmd = SSHRemoteCmd(host, get_random_port_cmd, is_forwarded=is_forwarded)
   r = remote_cmd.execute()
   ports = [int(p.strip()) for p in r.stdout.strip().split('\n') if p.strip().isdecimal()]
   if count == 1:
    return ports[0] if ports else None
   return ports

  raise TypeError(f'Expected host with type Node or string \'localhost\' but got {type(host)}')

 @classmethod
 def generate_ssh_keys(cls) -> Tuple[Path, Path]:
  """
  Генерирует ssh ключи

  :return: кортеж из путей до приватного и публичного ключей соответственно
  """
  path = Path(settings.MEDIA_ROOT)

  name = ''.join(random.choices(string.ascii_letters + string.digits, k=32))

  while name in [p.replace('.pub', '') for p in os.listdir(path)]:
   name = ''.join(random.choices(string.ascii_letters + string.digits, k=32))

  path = path.joinpath(name)
  public_key_path = path.parent.joinpath(f'{name}.pub')

  SSHKeyGenCmd(file_path=path).execute()

  if not path.exists():
   raise AnonAppException(f'Failed to generate ssh keys [private]')

  if not public_key_path.exists():
   os.remove(path)
   raise AnonAppException(f'Failed to generate ssh keys [public]')

  return path, public_key_path

 @classmethod
 def ansible_ping(cls, host: Node, is_forwarded=True) -> Result:
  """
  Отвечает pong если всё ок

  :param host: хост
  :param is_forwarded: пробрешенно ли соединение до этого хоста или нет
  """

  plb_path = Path(DATA_PREFIX, 'anon_app/ansible-playbooks/ping.yml')

  with AnsiblePlaybookCmd(plb_path, node=host, is_forwarded=is_forwarded) as cmd:
   result = cmd.execute()

  return result

 # noinspection DuplicatedCode
 @classmethod
 def set_iptables_masquerade(
   cls, network: Union[ipaddress.IPv4Network, ipaddress.IPv6Network]
 ):
  interface_cmd = PureCmd('ip route list | grep default | awk \'{print $5}\'')
  interface = interface_cmd.execute().stdout.strip()
  if not interface:
   raise ValueError('Can not set_iptables_masquerade: not found interface of default route')
  elif '\n' in interface:
   raise ValueError(f'Can not set_iptables_masquerade: many interfaces of default route [{interface}]')

  iptables_cmd = PureCmd(
   'if [[ -z $(iptables --table nat --list | grep "^MASQUERADE.*" | awk \'{print $4}\' '
   f'| grep \'{network.compressed}\') ]]; then iptables -t nat -A POSTROUTING '
   f'-o \'{interface}\' -s \'{network.compressed}\' -j MASQUERADE; fi;'
  )
  iptables_cmd.execute()

 @classmethod
 def enable_ip_forwarding(cls, host: Union[Node, str] = 'localhost', ipv6=False, is_forwarded=True):
  ipv = 'ipv6' if ipv6 else 'ipv4'
  cmd = PureCmd(f'sysctl -w net.{ipv}.ip_forward=1')

  if host == 'localhost':
   return cmd.execute().stdout.strip()

  return SSHRemoteCmd(host, cmd, is_forwarded=is_forwarded).execute()

 @classmethod
 def is_proxy_alive(cls, proxy: Proxy) -> Dict[str, Union[bool, str, None]]:
  if proxy.chain is None:
   return {
    'alive': None,
    'status': f'[{proxy.id}] can\'t check, chain is required to check the proxy'
   }

  remote_check = CmdChain(CheckProxy(proxy, proxy.chain.exit_node))
  result = remote_check.run(False)

  if result is None:
   return {'alive': False, 'status': f'[{proxy}] failed'}

  status_code = list(result.values())[-1].stdout.split(' ')[2].strip()

  if not status_code.isdecimal():
   return {'alive': True, 'status': f'[{proxy}] ok'}

  status_code = int(status_code)

  return {'alive': 200 <= status_code < 400, 'status': f'[{proxy}] ok'}

 @classmethod
 def get_host_country(cls, node: Node, is_forwarded=True) -> str:
  AptInstallPlaybookCmd(node=node, packages=['whois'], is_forwarded=is_forwarded).execute()
  cmd = GetHostCountry(node, is_forwarded=is_forwarded)
  result = cmd.execute()
  return result.stdout.strip()

 @classmethod
 def get_port_rtt(
   cls, target_host, target_port,
   host: Union[Node, str] = 'localhost',
   is_forwarded=True
 ):
  cmd = PureCmd(f'hping3 -S -c 1 -p {target_port} {target_host}')

  if host != 'localhost':
   AptInstallPlaybookCmd(node=host, packages=['hping3'], is_forwarded=is_forwarded).execute()
   cmd = SSHRemoteCmd(host, cmd, is_forwarded=is_forwarded)

  result = cmd.execute()

  return result.stdout.strip().split('\n')[-1].split('rtt=')[-1]

 @classmethod
 def get_ssh_connection_speed(
   cls, target_node: Node,
   host: Union[Node, str] = 'localhost',
   is_forwarded_src=True, is_forwarded_target=False
 ):
  dd_in_cmd = PureCmd('dd if=/dev/urandom bs=1048576 count=100')
  dd_out_cmd = PureCmd('dd of=/dev/null')
  cat_to_null_cmd = PureCmd('cat >/dev/null')

  if host == 'localhost':
   upload_test_cmd = SSHRemoteCmd(target_node, cat_to_null_cmd, is_forwarded=is_forwarded_target)
   download_test_cmd = SSHRemoteCmd(target_node, dd_in_cmd, is_forwarded=is_forwarded_target)

   upload_test_cmd = PureCmd(
    dd_in_cmd.serialize()[0] + ' | '
    + upload_test_cmd.serialize()[0].strip(';')
   )
   download_test_cmd = PureCmd(
    download_test_cmd.serialize()[0].strip(';')
    + ' | ' + dd_out_cmd.serialize()[0]
   )
  else:
   upload_test_cmd = PureCmd(
    f'{dd_in_cmd.serialize()[0].strip(";")} | '
    f'sshpass -p "$password" ssh -oStrictHostKeyChecking=no -p {target_node.server.ssh_port}'
    f'{target_node.server.server_account.username}@{target_node.server.ssh_ip}'
    f' "{cat_to_null_cmd.serialize()[0]}"',
    env={'password': target_node.server.server_account.password}
   )
   download_test_cmd = PureCmd(
    f'sshpass -p "$password" ssh -oStrictHostKeyChecking=no '
    f'-p {target_node.server.ssh_port} '
    f'{target_node.server.server_account.username}@{target_node.server.ssh_ip} '
    f'"{dd_in_cmd.serialize()[0]}" | {dd_out_cmd.serialize()[0]}',
    env={'password': target_node.server.server_account.password}
   )

   AptInstallPlaybookCmd(['sshpass'], host, is_forwarded=is_forwarded_src).execute()

   upload_test_cmd = SSHRemoteCmd(
    host, upload_test_cmd, is_forwarded=is_forwarded_src
   )
   download_test_cmd = SSHRemoteCmd(
    host, download_test_cmd, is_forwarded=is_forwarded_src
   )

  upload_result, download_result = upload_test_cmd.execute(), download_test_cmd.execute()

  return [
   ' '.join(r.stderr.strip().split('\n')[-1].split(' ')[-2:])
   for r in [upload_result, download_result]
  ]

 @classmethod
 def get_chain_ports_status(cls, exit_node: Node, is_forwarded=True) -> dict:
  install_nmap_in_container_cmd = PureCmd('docker exec external-worker_celery_1 apt install nmap -y')
  SSHRemoteCmd(exit_node, install_nmap_in_container_cmd, is_forwarded=is_forwarded).execute()

  ports = [
   settings.ANON_APP_EXTERNAL_REDIS_PORT, settings.ANON_APP_EXTERNAL_RABBITMQ_PORT,
   LOGSTASH_EXTERNAL_CONF['port'], EXTERNAL_SECOND_PG_PORT,
   LOGSTASH_EXTERNAL_FILEBEAT_CONF['port'], settings.ANON_APP_EXTERNAL_AVAGEN_PORT
  ]
  ports = ','.join([str(p) for p in ports])
  is_ports_open_cmd = PureCmd(
   f'docker exec external-worker_celery_1 nmap openssh -p {ports} | grep "^[0-9]*/"'
  )

  result = SSHRemoteCmd(exit_node, is_ports_open_cmd, is_forwarded=is_forwarded).execute()
  result = result.stdout.strip().split('\n')

  return {pl.split('/')[0]: pl.split(' ')[1] for pl in result}


class FlowerApi:
 def __init__(self):
  self.session = requests.session()
  self.base_flower_uri = 'http://celery-flower:5555/flower/'
  self.workers_url = urljoin(self.base_flower_uri, 'api/workers')
  self.dashboard_url = urljoin(self.base_flower_uri, 'dashboard')

 def get_dashboard_workers(self) -> List[dict]:
  """
  Получение воркеров с названием воркера и наименование очереди
  Пример ответа от /dashboard
  {
   "data": [
   {
    "worker-online": 1,
    "worker-heartbeat": 32872,
    "task-received": 15,
    "task-started": 15,
    "task-succeeded": 15,
    "hostname": "celery@celery-botfarm-internal",
    "pid": 9,
    "freq": 2.0,
    "heartbeats": [
    1643273167.91501,
    ...
    ],
    "clock": 830549,
    "active": 0,
    "processed": 15,
    "loadavg": [
    7.24,
    ...
    ],
    "sw_ident": "py-celery",
    "sw_ver": "4.4.2",
    "sw_sys": "Linux",
    "status": true
   },
   ...
  }

  """
  response = self.session.get(self.dashboard_url, params={'json': '1'})
  try:
   response.raise_for_status()
  except requests.exceptions:
   logger.exception(f'Catch exception. {response=}')
   raise
  return response.json()['data']

 def get_all_workers(self) -> dict:
  """
  Получение воркеров с названием воркера и статус воркера
  Пример ответа от api/workers

  {
   "celery@79ba63fcc3d4": {
    "stats": {
     "total": {},
     "pid": 8,
     "clock": "827422",
     "pool": {
     "max-concurrency": 8,
     "processes": [
      32,
      ...
     ],
     "max-tasks-per-child": 1,
     "put-guarded-by-semaphore": false,
     "timeouts": [
      0,
      0
     ],
     "writes": {
      "total": 0,
      "avg": "0.00%",
      "all": "",
      "raw": "",
      "strategy": "fair",
      "inqueues": {
      "total": 8,
      "active": 0
      }
     }
     },
     "broker": {
     "hostname": "openssh",
     "userid": "guest",
     "virtual_host": "/",
     "port": 5672,
     "insist": false,
     "ssl": false,
     "transport": "amqp",
     "connect_timeout": 4,
     "transport_options": {},
     "login_method": "AMQPLAIN",
     "uri_prefix": null,
     "heartbeat": 120.0,
     "failover_strategy": "round-robin",
     "alternates": []
     },
     "prefetch_count": 8,
     "rusage": {
     "utime": 1008.690007,
     "stime": 164.104345,
     "maxrss": 237288,
     "ixrss": 0,
     "idrss": 0,
     "isrss": 0,
     "minflt": 251306,
     "majflt": 68,
     "nswap": 0,
     "inblock": 11208,
     "oublock": 2136,
     "msgsnd": 0,
     "msgrcv": 0,
     "nsignals": 0,
     "nvcsw": 593278,
     "nivcsw": 65896
     }
    },
    "timestamp": 1643272159.705617,
    "active_queues": [
     {
     "name": "queue_name-fssp-ru",
     "exchange": {
      "name": "queue_name-fssp-ru",
      "type": "direct",
      "arguments": null,
      "durable": true,
      "passive": false,
      "auto_delete": false,
      "delivery_mode": null,
      "no_declare": false
     },
     "routing_key": "queue_name-fssp-ru",
     "queue_arguments": {
      "x-max-priority": 100
     },
     "binding_arguments": null,
     "consumer_arguments": null,
     "durable": true,
     "exclusive": false,
     "auto_delete": false,
     "no_ack": false,
     "alias": null,
     "bindings": [],
     "no_declare": null,
     "expires": null,
     "message_ttl": null,
     "max_length": null,
     "max_length_bytes": null,
     "max_priority": null
     }
    ],
    "registered": [
     "anon_app.tasks.tasks.add_client4ovpn_server",
     ...
    ],
    "scheduled": [],
    "active": [],
    "reserved": [],
    "revoked": [],
    "conf": {
     "broker_url": "amqp://guest:********@openssh:5672//",
     "result_backend": "redis://openssh:6379/1",
     "task_routes": [
     "sos_tasks.routing.TaskRouter"
     ],
     "task_queue_max_priority": 100,
     "task_default_priority": 50,
     "worker_prefetch_multiplier": 1,
     "result_extended": true,
     "task_track_started": true,
     "call_before_run_periodic_task": [
     "manage_app.utils.save_task_ids"
     ],
     "task_acks_late": true,
     "ONCE": {
     "backend": "celery_once.backends.Redis",
     "settings": {
      "url": "redis://openssh:6379/1",
      "default_timeout": 36000
     }
     },
     "include": [
     "anon_app.tasks.tasks",
     "lemmings_app.tasks",
     "ledger_app.tasks",
     "celery.app.builtins",
     "manage_app.tasks.tasks"
     ]
    }
    },
    ...
  }
  """
  response = self.session.get(self.workers_url, params={'refresh': '1'})
  try:
   response.raise_for_status()
  except requests.exceptions:
   logger.exception(f'Catch exception. {response=}')
   raise
  return response.json()


def prepare_proxies(proxies: Union[list, None], has_proxies_chain: bool) -> str:
 """Get args of proxy for tasks.

 Args:
  proxies: list of alive proxies chosen for lemmings task, can be also None.
  has_proxies_chain: whether proxy chain in used.

 Returns:
  string representation of proxy.
 """
 proxy = random.choice(proxies) if proxies else None
 if has_proxies_chain:
  return f'{MICROSOCKS_PROTOCOL}://{MICROSOCKS_IP}:{MICROSOCKS_PORT}'
 elif proxy:
  return ProxyForSessions(proxy['fields']).proxy_to_string()