import ipaddress
import logging
import os.path
from collections import defaultdict
from typing import List, Union, Optional

from django.core.exceptions import ObjectDoesNotExist, ValidationError as AttributeValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils.translation import gettext_lazy
from rest_framework.exceptions import ValidationError

from anon_app.conf import settings
from ledger_app.models import Account, PaidService
from soi_app.settings import SOS_PROXY_CHECK_LOCATION_URL, SOS_PROXY_CHECK_URL

logger = logging.getLogger(__name__)


class Hosting(PaidService):
 class Meta:
  ordering = ['-id']
  verbose_name = gettext_lazy('Hosting')
  verbose_name_plural = gettext_lazy('Hostings')

 def __str__(self):
  return f'{self.name}'


class HostingAccount(Account):
 class Meta:
  verbose_name = gettext_lazy('Hosting account')
  verbose_name_plural = gettext_lazy('Hosting accounts')

 hosting = models.OneToOneField(
  'Hosting',
  on_delete=models.CASCADE,
  null=False,
  related_name='hosting_account',
  verbose_name=gettext_lazy('hosting')
 )

 def __str__(self):
  return f'{self.username} on {self.hosting.name} [id: {self.id}]'


class Server(models.Model):
 class Meta:
  ordering = ['-id']
  constraints = [
   models.UniqueConstraint(fields=['ssh_ip'], name='unique ip')
  ]

  verbose_name = gettext_lazy('Server')
  verbose_name_plural = gettext_lazy('Servers')

 is_powerful = models.BooleanField(verbose_name=gettext_lazy('Is powerful?'), default=False)

 geo = models.CharField(max_length=128, default='', blank=True, verbose_name=gettext_lazy('geo'))
 hosting = models.ForeignKey('Hosting', on_delete=models.CASCADE, verbose_name=gettext_lazy('hosting'))
 ssh_ip = models.GenericIPAddressField(verbose_name=gettext_lazy('ip'))

 ssh_port = models.PositiveIntegerField(
  default=22,
  validators=[MinValueValidator(1), MaxValueValidator(65535)],
  verbose_name=gettext_lazy('ssh port')
 )

 ENTRY = 'Entry'
 OUTPUT = 'Output'
 INTERMEDIATE = 'Intermediate'
 TYPE_CHOICES = [
  (ENTRY, gettext_lazy('Entry')),
  (OUTPUT, gettext_lazy('Output')),
  (INTERMEDIATE, gettext_lazy('Intermediate')),
 ]

 type = models.CharField(
  blank=True,
  max_length=128,
  verbose_name=gettext_lazy('type'),
  choices=TYPE_CHOICES,
 )

 anonymization_chain = models.ForeignKey(
  'Chain',
  on_delete=models.SET_NULL,
  related_name='servers',
  verbose_name=gettext_lazy('anonymization chain'),
  null=True,
  blank=True,
  )

 @property
 def in_use(self):
  try:
   return self.node.in_use
  except ObjectDoesNotExist as _:
   return False

 @property
 def used_in(self):
  try:
   place = self.node.used_in
   return place.label if place is not None else None
  except ObjectDoesNotExist as _:
   return None

 def __str__(self):
  return f'{self.ssh_ip} on {self.hosting.name} '


class SrvAccount(models.Model):
 class Meta:
  ordering = ['-id']

  verbose_name = gettext_lazy('Server Account')
  verbose_name_plural = gettext_lazy('Server Accounts')

 username = models.CharField(max_length=32, verbose_name=gettext_lazy('username'))
 password = models.CharField(max_length=128, verbose_name=gettext_lazy('password'))
 server = models.OneToOneField(
  'Server',
  on_delete=models.CASCADE,
  related_name='server_account',
  verbose_name=gettext_lazy('server')
 )

 def __str__(self):
  return f'{self.username} from {self.server} [id: {self.id}]'


class OpenVPNClient(models.Model):
 class Meta:
  ordering = ['-id']
  verbose_name = gettext_lazy('OpenVPN client')
  verbose_name_plural = gettext_lazy('OpenVPN clients')

 node = models.ForeignKey(
  'Node',
  on_delete=models.CASCADE,
  related_name='ovpn_clients_set',
  verbose_name=gettext_lazy('Node')
 )

 sub_network = models.GenericIPAddressField(
  null=True, blank=True, verbose_name=gettext_lazy('Subnet which is routed to OpenVPN network')
 ) # /etc/openvpn/ccd/fullclientname
 sub_netmask = models.GenericIPAddressField(
  null=True, blank=True, verbose_name=gettext_lazy('Mask of subnet which is routed to OpenVPN network')
 ) # /etc/openvpn/ccd/fullclientname

 client = models.CharField(null=True, blank=True, max_length=128, verbose_name=gettext_lazy('OpenVPN client name'))
 config = models.FileField(
  null=True, blank=True, verbose_name=gettext_lazy('OpenVPN client config file')
 )
 client_ip = models.GenericIPAddressField(
  null=True, blank=True,
  verbose_name=gettext_lazy('Client IP in OpenVPN network')
 ) # /etc/openvpn/iip.txt

 is_private = models.BooleanField(default=False, verbose_name=gettext_lazy('is for access to private network'))
 celery_task_id = models.CharField(
  null=True, blank=True, max_length=128, verbose_name=gettext_lazy('Celery task id')
 )

 is_issued = models.BooleanField(
  default=False, verbose_name=gettext_lazy('Was issued'),
 )
 openvpn_client_user = models.CharField(
  blank=True, max_length=128, verbose_name=gettext_lazy('Issued to whom'),
 )

 def save(self, *args, **kwargs):
  """Customized method with auto removing openvpn_client_user when checkbox is off."""
  if not self.is_issued:
   self.openvpn_client_user = ''
  super(OpenVPNClient, self).save(*args, **kwargs)

 def __str__(self):
  return f'{self.client}@{"server" if self.is_private else self.client_ip} -> ' \
     f'(OVPN) -> <{self.node.server}> [{self.id}]'

 @property
 def is_valid(self):
  return None not in (self.config, self.client_ip)


class Node(models.Model):
 class Meta:
  ordering = ['-id']
  verbose_name = gettext_lazy('Node')
  verbose_name_plural = gettext_lazy('Nodes')

 class PlaceInChain(models.TextChoices):
  start = 1, 'start'
  middle = 0, 'middle'
  end = 2, 'end'

 # FIXME: починить загрузку файла сертификата
 server = models.OneToOneField('Server', on_delete=models.CASCADE, verbose_name=gettext_lazy('server'))

 id_rsa = models.FileField(null=True, blank=True, verbose_name=gettext_lazy('ssh private key')) # todo: хорошо бы зашифровать
 id_rsa_pub = models.FileField(null=True, blank=True, verbose_name=gettext_lazy('ssh public key'))
 ssh_proc_port = models.PositiveIntegerField(
  null=True, blank=True, validators=[MinValueValidator(1024), MaxValueValidator(65535)],
  verbose_name=gettext_lazy('ssh proc port')
 )

 ovpn_network = models.GenericIPAddressField(
  default='10.0.0.0', verbose_name=gettext_lazy('OpenVPN network')
 )
 ovpn_srv_ip = models.GenericIPAddressField(
  null=True, blank=True, verbose_name=gettext_lazy('Server IP in OpenVPN network')
 )
 ovpn_netmask = models.GenericIPAddressField(
  default='255.255.255.0', verbose_name=gettext_lazy('OpenVPN netmask')
 )
 ovpn_port = models.PositiveIntegerField(
  default=1194, validators=[MinValueValidator(1024), MaxValueValidator(65535)],
  verbose_name=gettext_lazy('OpenVPN port')
 )

 is_for_private_network = models.BooleanField(
  default=False,
  verbose_name=gettext_lazy('is for access to private network')
 ) #TODO: wtfovpn

 forwarded_zabbix_port = models.PositiveIntegerField(
  validators=[MinValueValidator(1024), MaxValueValidator(65535)], null=True,
  verbose_name=gettext_lazy('forwarded zabbix port'),
  default=settings.ANON_APP_EXTERNAL_ZABBIX_PORT
 )

 @property
 def edges(self):
  return Edge.objects.filter(Q(in_node=self) | Q(out_node=self))

 @property
 def is_powerful(self):
  return self.server.is_powerful

 @property
 def type(self):
  server_type = gettext_lazy(self.server.type)
  if server_type:
   server_type = gettext_lazy(server_type)
  return server_type

 @property
 def used_in(self) -> Union[PlaceInChain, 'None']:
  chains = Chain.objects.filter(Q(edges__in_node=self) | Q(edges__out_node=self)).distinct()

  if len(chains) > 1:
   logger.warning(f'Something strange, one node in some alive chains: {chains}')

  chains_nodes = [chain.sorted_nodes for chain in chains]

  position = None

  for nodes in chains_nodes:
   if position is None:
    position = self.PlaceInChain.middle
   if nodes[0] == self:
    position = self.PlaceInChain.start
   elif nodes[-1] == self:
    position = self.PlaceInChain.end

  return position

 @property
 def in_use(self):
  first_or_last = self.used_in == self.PlaceInChain.start or self.used_in == self.PlaceInChain.end

  if first_or_last:
   return True

  return self.is_for_private_network or Edge.objects.filter(
   (Q(in_node=self) | Q(out_node=self)) & ~Q(chain__status=Chain.StatusChoice.WORKER_DONT_RESPONSE)
  ).exists()

 @property
 def ovpn_network_full(self) -> Union[ipaddress.IPv4Network, ipaddress.IPv6Network]:
  return ipaddress.ip_network(f'{self.ovpn_network}/{self.ovpn_netmask}')

 @transaction.atomic()
 def save(
   self, force_insert=False, force_update=False,
   using=None, update_fields=None
 ):
  super(Node, self).save(force_insert, force_update, using, update_fields)
  self.validate()

  # noinspection PyBroadException,DuplicatedCode
  try:
   key_path = self.id_rsa.path
   pub_key_path = self.id_rsa_pub.path
   if os.path.exists(key_path) and os.path.exists(pub_key_path):
    return
   logger.warning(f'key not exists | goto generate_keys_for_node [node_id={self.id}]')
  except Exception as e:
   logger.warning(f'{e} | goto generate_keys_for_node [node_id={self.id}]')
   pass

  from anon_app.tasks.tasks import generate_keys_for_node

  generate_keys_for_node(
   node_id=self.pk, task_identifier=f'generate:node_ssh_keys:{self.pk}'
  )

 def validate(self):
  if (self.id_rsa is None) ^ (self.id_rsa_pub is None):
   raise ValidationError({
    'error': {
     'code': 3024,
     'description': f'You must either specify both keys, or do not specify any'
    }
   })

 @staticmethod
 def default_dict(srv: Server) -> dict:
  return {
   'server': srv,
   'id_rsa': None,
   'id_rsa_pub': None,
   'ssh_proc_port': None,
   'ovpn_network': '10.0.0.0',
   'ovpn_srv_ip': None,
   'ovpn_netmask': '255.255.255.0',
   'ovpn_port': 1194,
   'is_for_private_network': False # TODO: wtfovpn
  }

 def __str__(self):
  return f'geo: {self.server.geo}, server: {self.server}, port: {self.ssh_proc_port}'


class Edge(models.Model):
 class Meta:
  ordering = ['-id']
  # unique_together = ('in_node', 'out_node',) todo: musthave
  # "не использовать в разных цепочках одни и те же узлы"
  verbose_name = gettext_lazy('Edge')
  verbose_name_plural = gettext_lazy('Edges')

 class ProtocolChoice(models.TextChoices):
  SSH = 'SOCKS', 'SOCKS'
  SSH_VIA_TOR = 'TOR', 'TOR'
  VPN = 'VPN', 'VPN'

 in_node = models.ForeignKey(
  'Node',
  on_delete=models.CASCADE,
  related_name='in_node_edges',
  verbose_name=gettext_lazy('in_node')
 )

 out_node = models.ForeignKey(
  'Node',
  on_delete=models.CASCADE,
  related_name='out_node_edges',
  verbose_name=gettext_lazy('out_node')
 )

 protocol = models.CharField(
  max_length=64,
  choices=ProtocolChoice.choices,
  default=ProtocolChoice.SSH.value,
  verbose_name=gettext_lazy('protocol')
 )

 chain = models.ForeignKey(
  'Chain',
  on_delete=models.CASCADE,
  related_name='edges',
  verbose_name=gettext_lazy('chain')
 )

 ping = models.CharField(
  max_length=32, null=True, blank=True,
  verbose_name=gettext_lazy('Ping')
 )
 upload_speed = models.CharField(
  max_length=32, null=True, blank=True,
  verbose_name=gettext_lazy('Uploading speed')
 )
 download_speed = models.CharField(
  max_length=32, null=True, blank=True,
  verbose_name=gettext_lazy('Downloading speed')
 )

 def __str__(self):
  return f'[{self.out_node}] -> ({self.protocol}) -> [{self.in_node}] [id: {self.id}]'


class Chain(models.Model):
 class Meta:
  ordering = ['-id']
  verbose_name = gettext_lazy('Chain')
  verbose_name_plural = gettext_lazy('Chains')

 # todo: добавить проверку в TaskType и InternetResource, status != READY -> error
 class StatusChoice(models.TextChoices):
  # PRE_CREATING = 'PRE_CREATING', 'Предсоздаваемое состояние'
  CREATING = 'CREATING', 'В процессе создания'
  WORKER_DONT_RESPONSE = 'WORKER_DONT_RESPONSE', 'Воркер не отвечает'
  READY = 'READY', 'Готов к работе'
  CREATING_FAILED = 'CREATING_FAILED', 'Не удалось создать'
  BLOCK = 'BLOCK', 'Заблокирован'
  REBUILD_CONNECTION = 'REBUILD_CONNECTION', 'Соединение перестраивается'
  RELOAD_IMAGE = 'RELOAD_IMAGE', 'Загрузка нового образа'
  TEST_FROM_READY = 'TEST_FROM_READY', 'Тестируется (была жива)'
  TEST_FROM_DIED = 'TEST_FROM_DIED', 'Тестируется (была мертва)'
  DIED = 'DIED', 'Недоступен'
  TEST = 'TEST', 'Тестируется'

 title = models.CharField(max_length=120, verbose_name=gettext_lazy('title'))
 # todo: Удалить default и сделать task_queue_name `unique=True`(и обновить фикстуры),
 # когда сможем запускать воркеры на определенные очереди

 task_queue_name = models.CharField(max_length=120, unique=True, verbose_name=gettext_lazy('task queue name'))

 app_image = models.ForeignKey(
  'AppImage',
  null=True,
  blank=True,
  on_delete=models.DO_NOTHING,
  verbose_name=gettext_lazy('app image')
 )

 status = models.CharField(
  max_length=128,
  choices=StatusChoice.choices,
  default=StatusChoice.CREATING,
  verbose_name=gettext_lazy('status')
 )

 for_internet_access = models.BooleanField(
  default=False, verbose_name=gettext_lazy('Internet access chain')
 )

 need_pull_accounts = models.BooleanField(
  default=False, verbose_name=gettext_lazy('Is it necessary to create a pool of accounts?')
 )

 openvpn_config = models.FileField(
  null=True,
  blank=True,
  help_text='Конфигурационный файл OpenVPN для подключения к удаленному узлу',
  verbose_name=gettext_lazy('Configuration file OpenVPN')
 )

 openssh_container_id_rsa = models.FileField(
  null=True,
  help_text='Приватный ключ для доступа к удаленному '
     'контейнеру с openssh (если не указывать, то создастся автоматически)',
  verbose_name=gettext_lazy('ssh container private key')
 )

 openssh_container_id_rsa_pub = models.FileField(
  null=True,
  help_text='Публичный ключ для доступа к удаленному '
     'контейнеру с openssh (если не указывать, то создастся автоматически)',
  verbose_name=gettext_lazy('ssh container public key')
 )

 openssh_container_external_port = models.PositiveIntegerField(
  validators=[MinValueValidator(1024), MaxValueValidator(65535)], null=True,
  help_text='Порт на котором будет доступен ssh сервер контейнера openssh на удаленном хосте',
  verbose_name=gettext_lazy('external ssh container port')
 )

 openssh_container_internal_port = models.PositiveIntegerField(
  validators=[MinValueValidator(1024), MaxValueValidator(65535)], null=True,
  verbose_name=gettext_lazy('internal ssh container port')
 )

 ping = models.CharField(
  max_length=32, null=True, blank=True,
  verbose_name=gettext_lazy('Ping')
 )
 upload_speed = models.CharField(
  max_length=32, null=True, blank=True,
  verbose_name=gettext_lazy('Uploading speed')
 )
 download_speed = models.CharField(
  max_length=32, null=True, blank=True,
  verbose_name=gettext_lazy('Downloading speed')
 )
 ports_info = models.JSONField(
  default=dict, blank=True,
  verbose_name=gettext_lazy('Port states')
 )

 last_update_info_dt = models.DateTimeField(
  null=True, blank=True,
  verbose_name=gettext_lazy('Last testing datetime')
 )

 last_checking_celery_task_id = models.UUIDField(
  null=True, blank=True, verbose_name='ID задачи тестирования celery'
 )

 proxy_limit = models.PositiveSmallIntegerField(
  default=10, verbose_name=gettext_lazy('Proxy limit')
 )

 concurrency = models.PositiveIntegerField(
  default=0, verbose_name=gettext_lazy('concurrency'), help_text=gettext_lazy('default 0 - one thread one core'),
  validators=[
   MaxValueValidator(32767),
  ]
 )

 check_proxy_limit = models.BooleanField(
  default=False, verbose_name=gettext_lazy('Check proxy limit')
 )

 has_proxies_chain = models.BooleanField(
  default=False, verbose_name=gettext_lazy('Use proxies chain'),
 )

 proxies_in_chain = models.PositiveIntegerField(
  default=0, verbose_name=gettext_lazy('Amount of proxies for building proxies chain'),
  validators=[
   MaxValueValidator(5),
  ]
 )

 def get_alive_proxies_query_with_conditions(self, bot_pk: Optional[int] = None):
  """
   Args:
    bot_pk: int - опциональный аргумент. pk аккаунта бота, используемого для скрапера.
    Нужен для дополнительной фильтрации проксей по стране

   Return: Возвращает queryset прокси по фильтру
  """

  from lemmings_app.models import BotAccount

  alive_proxies = self.proxy_set.get_alive_proxies()

  if bot_pk is not None:
   location = BotAccount.objects.get(pk=bot_pk).location
   if location is None:
    return alive_proxies
   return alive_proxies.filter(location=location)
  return alive_proxies

 def create_tasks_chain_for_proxies(self, proxies: List[dict], check_proxies_location: bool = False):
  from anon_app.tasks.tasks import (
   async_are_proxies_alive, update_proxies, check_proxy_location, set_proxies_state)

  set_proxy_checking_state_signature = set_proxies_state.s(
   proxies=proxies, state=Proxy.StateChoice.CHECKING, queue_name=self.task_queue_name,
   is_internal=True, task_identifier=async_are_proxies_alive.__name__,
  )
  async_are_proxies_alive_signature = async_are_proxies_alive.s(
   check_url=SOS_PROXY_CHECK_URL, queue_name=self.task_queue_name,
   is_internal=False, task_identifier=async_are_proxies_alive.__name__,
  )
  update_proxies_signature = update_proxies.s(
   queue_name=self.task_queue_name, is_internal=True, task_identifier=update_proxies.__name__,
  )
  tasks_chain = set_proxy_checking_state_signature | async_are_proxies_alive_signature
  if check_proxies_location:
   check_proxy_location_signature = check_proxy_location.s(
    check_location_url=SOS_PROXY_CHECK_LOCATION_URL,
    queue_name=self.task_queue_name,
    task_identifier=check_proxy_location.__name__,
   )
   tasks_chain |= check_proxy_location_signature

  return tasks_chain | update_proxies_signature

 @property
 def exit_node(self) -> Union[Node, None]:
  edges = self.sorted_edges
  if not edges:
   return None
  return edges[-1].in_node

 @property
 def sorted_nodes(self) -> List[Node]:
  edges = self.sorted_edges

  if not edges:
   return []

  nodes = [edge.out_node for edge in edges]
  nodes.append(edges[-1].in_node)

  return nodes

 @property
 def sorted_edges(self) -> List[Edge]:
  return self.get_validated_sorted_edges(validate=False)

 def validate_edges(self) -> 'None':
  self.get_validated_sorted_edges(validate=True)

 def get_validated_sorted_edges(self, validate=True) -> List[Edge]:
  sorted_edges = []

  out_node_ids = list(self.edges.values_list('out_node_id', flat=True))
  in_node_ids = list(self.edges.values_list('in_node_id', flat=True))
  is_one_node = in_node_ids == out_node_ids and len(in_node_ids) == 1
  is_two_node = len(in_node_ids + out_node_ids) == 2

  if validate and len(set(out_node_ids + in_node_ids)) < settings.ANON_APP_MIN_CHAIN_SIZE and not (
    is_one_node or is_two_node):
   raise ValidationError({
    'error': {
     'code': 3020,
     'description': f'Min size of chain is {settings.ANON_APP_MIN_CHAIN_SIZE}'
    }
   })

  if validate and len(out_node_ids) != len(set(out_node_ids)):
   raise ValidationError({
    'error': {
     'code': 3025,
     'description': 'Using a node twice as out'
    }
   })

  if validate and len(in_node_ids) != len(set(in_node_ids)):
   raise ValidationError({
    'error': {
     'code': 3026,
     'description': 'Using a node twice as in'
    }
   })

  start_node_id = set(out_node_ids) - set(in_node_ids)
  end_node_id = set(in_node_ids) - set(out_node_ids)

  if is_one_node:
   return [self.edges.get(out_node_id=in_node_ids[0])]

  if validate and (len(start_node_id) != 1 or len(end_node_id) != 1):
   raise ValidationError({
    'error': {
     'code': 3026,
     'description': 'Chain have breaks'
    }
   })

  node_id = list(start_node_id)[0]
  end_node_id = list(end_node_id)[0]

  while node_id != end_node_id:
   edge = self.edges.filter(out_node_id=node_id).last()
   sorted_edges.append(edge)
   node_id = edge.in_node_id

  return sorted_edges

 def get_nodes_ip_list(self):
  """

  :return: Возвращает ноды и их ip адреса, ассоциированные с цепочкой chain_pk
  """
  edges = Edge.objects.filter(chain=self)
  nodes = Node.objects.filter(Q(in_node_edges__in=edges) | Q(out_node_edges__in=edges))
  servers = Server.objects.filter(node__in=nodes)
  return [{'node': s.node.pk, 'ip': s.ssh_ip} for s in servers]

 def validate_image(self):
  if self.app_image is None:
   raise ValidationError({
    'error': {
     'code': 3028,
     'description': 'app_image must not be null'
    }
   })

 def validate_keys(self):
  if (self.openssh_container_id_rsa is None) ^ (self.openssh_container_id_rsa_pub is None):
   raise ValidationError({
    'error': {
     'code': 3023,
     'description': f'You must either specify both keys, or do not specify any'
    }
   })

 def clean(self):
  """Customized method for checking model fields.
  Check if attribute has_proxies_chain is consistent with attribute proxies_in_chain.

  Raises:
   ValidationError: in case of inconsistency of has proxies boolean with proxies amount in chain.
  """
  if self.has_proxies_chain and self.proxies_in_chain == 0:
   raise AttributeValidationError(
    'Обязательно выберите количество прокси в цепочке, если хотите использовать цепочку из прокси серверов',
   )
  if not self.has_proxies_chain and self.proxies_in_chain > 0:
   raise AttributeValidationError(
    'Количество прокси серверов в цепочке не может быть больше нуля, '
    + 'если цепочка из прокси серверов не используется',
   )

 def validate(self):
  self.validate_keys()
  self.validate_image()
  self.validate_edges()

  # TODO: wtfovpn
  # if not self.is_for_private_network:
  #  return
  #
  # for edge in edges:
  #  if edge.protocol != Edge.ProtocolChoice.VPN:
  #   raise ValidationError({
  #    'error': {
  #     'code': 3030,
  #     'description': f'All edge\'s protocol must be VPN if is_for_private_network - True'
  #    }
  #   })

 def save(self, force_insert=False, force_update=False, using=None, update_fields=None):
  super(Chain, self).save(
   force_insert=force_insert, force_update=force_update,
   using=using, update_fields=update_fields
  )

  # noinspection DuplicatedCode
  try:
   key_path = self.openssh_container_id_rsa.path
   pub_key_path = self.openssh_container_id_rsa_pub.path
   if os.path.exists(key_path) and os.path.exists(pub_key_path):
    return
   logger.warning(f'keys not exists | goto generate_keys_for_chain [chain_id={self.id}]')
  except Exception as e:
   logger.warning(f'{e} | goto generate_keys_for_chain [chain_id={self.id}]')
   pass

  from anon_app.tasks.tasks import generate_keys_for_chain

  generate_keys_for_chain(
   chain_id=self.pk, task_identifier=f'generate:chain_ssh_keys:{self.pk}'
  )

 def __str__(self):
  return f'{self.title} [{self.StatusChoice[self.status].label}]'


class AppImage(models.Model):
 class Meta:
  ordering = ['-id']
  verbose_name = gettext_lazy('App Image')
  verbose_name_plural = gettext_lazy('App Images')

 title = models.CharField(
  max_length=120,
  help_text='Название образа, допустимо любое значение',
  verbose_name=gettext_lazy('title')
 )

 name = models.CharField(
  max_length=120,
  help_text='Имя образа, данное ему при создании (пр.: gitlab.lan:5005/filigree/sos/sos_web-app)',
  verbose_name=gettext_lazy('image name')
 )

 image = models.FileField(
  help_text='ZIP архив, содержащий в себе tar файл, который представляет собой docker образ приложения',
  verbose_name=gettext_lazy('image')
 )

 env = models.FileField(
  help_text='Текстовый файл формата env, описывает переменные окружающей среды, '
     'брать актуальную версию можно из /path/to/soi/end-node',
  verbose_name=gettext_lazy('environment variable')
 )

 docker_compose = models.FileField(
  help_text='Текстовый файл в формате yml, описывает запуск сервисов, '
     'брать актуальную версию можно из /path/to/soi/end-node',
  verbose_name=gettext_lazy('docker-compose')
 )

 # загружается на удаленный узел и распаковывается в директорию env.LEMMINGS_BROWSER_PROFILES_PATH
 browser_profiles = models.FileField(
  help_text='ZIP архив с профилями браузеров (в архиве должны содержаться две директории: '
     'firefox и chrome, каждая из которых в свою очередь содержит некоторое кол-во '
     'директорий, являющихся профилями соответствующих браузеров)',
  verbose_name=gettext_lazy('browser profiles')
 )

 filebeat_config = models.FileField(
  help_text='Файл в формате yml для получение сообщений из файлов журналов удалённых узлов',
  verbose_name=gettext_lazy('filebeat config')
 )

 def __str__(self):
  return f'{self.title}: {self.image.path} [id: {self.id}]'


class ProxyManager(models.Manager):
 def get_alive_proxies(self):
  return self.filter(state='ALIVE').filter(
   (Q(number_of_applying='DISPOSABLE') & Q(applying='UNUSED')) |
   (Q(number_of_applying='REUSABLE') & ~Q(applying='BLACKLIST'))
  )

 def get_statistics(self) -> dict:
  proxies = self.all().values('location', 'state')

  proxies_info = defaultdict(lambda: {
   'proxies_count': 0,
   'alive_proxies_count': 0,
   'dead_proxies_count': 0,
  })
  for proxy in proxies:
   proxy_location = proxies_info[proxy['location']]
   proxy_location['proxies_count'] += 1
   proxy_location['alive_proxies_count'] += int(proxy['state'] == 'ALIVE')
   proxy_location['dead_proxies_count'] += int(proxy['state'] == 'DIED')

  return proxies_info


class Proxy(models.Model):
 class Meta:
  ordering = ['-id']
  verbose_name = gettext_lazy('Proxy')
  verbose_name_plural = gettext_lazy('Proxies')

 class ProtocolChoice(models.TextChoices):
  EMPTY = '', '---------'
  UNKNOWN_PROTOCOL = 'unknown', 'неизвестно'
  HTTP = 'http', 'http'
  HTTPS = 'https', 'https'
  Socks4 = 'socks4', 'socks4'
  Socks5 = 'socks5', 'socks5'

 class StateChoice(models.TextChoices):
  ALIVE = 'ALIVE', gettext_lazy('alive')
  DIED = 'DIED', gettext_lazy('died')
  UNKNOWN = 'UNKNOWN', gettext_lazy('unknown')
  CHECKING = 'CHECKING', gettext_lazy('checking')
  CHECKING_FAILED = 'CHECKING_FAILED', gettext_lazy('checking failed')

 class SecureFlagChoice(models.TextChoices):
  PAID = 'PAID', gettext_lazy('Paid proxy'),
  FREE = 'FREE', gettext_lazy('Free proxy'),

 class ApplyingChoice(models.TextChoices):
  USED = 'USED', gettext_lazy('Used proxy')
  UNUSED = 'UNUSED', gettext_lazy('Unused proxy')
  BLACKLIST = 'BLACKLIST', gettext_lazy('Blacklist')

 class NumberOfApplyingChoice(models.TextChoices):
  DISPOSABLE = 'DISPOSABLE', gettext_lazy('Disposable proxy')
  REUSABLE = 'REUSABLE', gettext_lazy('Reusable proxy')

 class ImportCsvFormatChoice(models.TextChoices):
  IP_PORT = 'IP:port', 'IP:port'
  IP_PORT_LOGIN_PASSWORD = 'IP:port:login:password', 'IP:port:login:password'
  LOGIN_PASSWORD_IP_PORT_LOCATION = 'login:password:IP:port:location', 'login:password:IP:port:location'

 objects = ProxyManager()

 # csv_file = models.FileField(help_text='Файл csv с данным о proxy серверах')
 protocol = models.CharField(
  choices=ProtocolChoice.choices, max_length=128,
  verbose_name=gettext_lazy('protocol')
 )

 username = models.CharField(
  max_length=128, blank=True, null=True,
  verbose_name=gettext_lazy('username')
 )

 password = models.CharField(
  max_length=128, blank=True, null=True,
  verbose_name=gettext_lazy('password')
 )

 ip = models.CharField(
  max_length=128,
  verbose_name=gettext_lazy('ip')
 )

 port = models.CharField(
  max_length=128,
  verbose_name=gettext_lazy('port')
 )

 location = models.CharField(
  max_length=128,
  verbose_name=gettext_lazy('location')
 )

 chain = models.ForeignKey(
  Chain, verbose_name=gettext_lazy('chain'), null=True, on_delete=models.SET_NULL,
 )

 state = models.CharField(
  max_length=128, blank=True, default=StateChoice.UNKNOWN.value,
  choices=StateChoice.choices, verbose_name=gettext_lazy('Proxy state')
 )

 secure_flag = models.CharField(
  verbose_name=gettext_lazy('Secure flag'), max_length=4, blank=True, choices=SecureFlagChoice.choices
 )

 applying = models.CharField(
  verbose_name=gettext_lazy('Applying'), max_length=128, choices=ApplyingChoice.choices
 )

 number_of_applying = models.CharField(
  verbose_name=gettext_lazy('Number of applying'), max_length=128, choices=NumberOfApplyingChoice.choices
 )

 source = models.CharField(
  max_length=128, verbose_name=gettext_lazy('source'), blank=True
 )

 services = models.JSONField(
  verbose_name=gettext_lazy('services'), blank=True, default=dict
 )

 comment = models.TextField(
  verbose_name=gettext_lazy('Comment'), blank=True,
 )

 last_check_dt = models.DateTimeField(
  null=True, blank=True,
  verbose_name=gettext_lazy('Last proxy testing datetime')
 )

 last_successful_check_dt = models.DateTimeField(
  null=True, blank=True,
  verbose_name=gettext_lazy('Last successful proxy testing datetime')
 )

 def __str__(self):
  if self.applying:
   applying = f"\tиспользование {self.ApplyingChoice.__getattr__(self.applying).label}"
  else:
   applying = ''
  if self.username and self.password:
   result = (f'{self.protocol}://{self.username}:{self.password}@{self.ip}:{self.port}\tместоположение: '
      f'{self.location}{applying}\tисточник {self.source}')
  else:
   result = f'{self.protocol}://{self.ip}:{self.port}\tместоположение: {self.location}{applying}\tисточник {self.source}'
  return result

 @property
 def host_port(self):
  return f'{self.ip}:{self.port}'

 def clean(self):
  """Customized method for checking model fields.
  Checks the proxy can be added to the chain.

  Raises:
   ValidationError: when trying to add a not ALIVE proxy to the chain.
  """
  if self.chain and self.state != self.StateChoice.ALIVE:
   raise AttributeValidationError(
    f'Невозможно привязать прокси сервер {self.ip}:{self.port} со статусом "{self.get_state_display()}" к цепочке анонимизации.')