import datetime
import json
import logging
import random
import traceback
from typing import List

from celery_once import QueueOnce
from django.conf import settings
from django.core import serializers
from django.db.models import Q

from anon_app.exceptions import ChainHasNoAliveProxies, CmdError
from anon_app.models import Chain, Edge, Node, OpenVPNClient, Proxy
from anon_app.proxy import ProxyChecker
from anon_app.tasks.utils import ChainCtl, CmdCtl, FlowerApi, OpenVPNCtl, build_openvpn_network, check_nodes_quantity
from notifications_app.models import Notification
from soi_tasks.core import app


logger = logging.getLogger(__name__)


@app.task
def pre_build_chain(chain_id: int, chain_status: Chain.StatusChoice, *args, **kwargs):
 """Проставляет статус цепочке в БД.

 :param chain_id: ID создаваемой цепочки в БД
 :param chain_status: Статус цепочки
 """
 Chain.objects.filter(id=chain_id).update(status=chain_status)


@app.task
def post_build_chain(_, chain_id: int, msg: str, *args, **kwargs):
 """Проставляет статус цепочке в БД после ее обновления.

 :param _: Результат предыдущей задачи
 :param chain_id: ID создаваемой цепочки в БД
 :param msg: Сообщение для отправки уведомления пользователю
 """
 chain = Chain.objects.get(id=chain_id)
 chain.status = Chain.StatusChoice.READY
 chain.save(update_fields=['status'])
 Notification.send_to_all(
  content=msg.format(chain.title),
  log_level=Notification.LogLevelChoice.COLOR_SUCCESS.value,
 )


@app.task
def callback_build_chain_errors(request, exc, traceback, chain_id: int):
 """Выполнение ряда процедур в случае ошибки при построении цепочки.

 :param request: Контекст цепочки задач
 :param exc: Экземпляр класса Exception, вызванный в процессе выполнения цепочки задач
 :param traceback: Трейсбэк ошибки, вызванной в процессе выполнения цепочки задач
 :param chain_id: ID создаваемой цепочки в БД
 """
 chain = Chain.objects.get(id=chain_id)
 chain_ctl = ChainCtl(chain)
 chain.status = Chain.StatusChoice.CREATING_FAILED
 chain.save(update_fields=['status'])
 Notification.send_to_all(
  content=f'Построении цепочки {chain.title}, завершилось с ошибкой',
  log_level=Notification.LogLevelChoice.COLOR_DANGER.value,
  error=f'{exc}',
  traceback=traceback,
 )
 logger.error(exc)
 chain_ctl.kill_connection_proc().run(raise_exc=False)


@app.task
def forward_ports_to_priority_celery_queue_after_building(_, chain_id: int, *args, **kwargs):
 """Запускает задачу проброса портов для priority_celery_internal

 :param _: результат предыдущей задачи
 :param chain_id: id создаваемой цепочки в БД
 """
 chain = Chain.objects.get(id=chain_id)
 if chain.for_internet_access:
  return

 logger.info(f'Start set up priority_celery_queue for chain: {chain}')
 chain_ctl = ChainCtl(chain)
 chain_ctl.port_forwarding_for_priority_celery_queue()


@app.task
def forward_ports_to_priority_celery_queue_after_rebuilding(_, chain_id: int, *args, **kwargs):
 """Запускает задачу проброса портов для priority_celery_internal

 :param _: результат предыдущей задачи
 :param chain_id: id создаваемой цепочки в БД
 """
 chain = Chain.objects.get(id=chain_id)
 logger.info(f'Start forward ports to priority_celery_queue for chain: {chain}')
 chain_ctl = ChainCtl(chain)
 chain_ctl.port_forwarding_for_priority_celery_queue()


@app.task
def build_chain(_, chain_id: int, *args, **kwargs):
 """
 Запускает задачу создания цепочки анонимизации

 :param _: результат предыдущей задачи
 :param chain_id: id создаваемой цепочки в БД
 """
 chain = Chain.objects.get(id=chain_id)
 logger.info(f'Start building chain: {chain}')
 chain_ctl = ChainCtl(chain)

 srv_node, need_port_forwarding = check_nodes_quantity(chain)

 if chain.for_internet_access:
  OpenVPNCtl.prebuild_openvpn_conf_one_node(chain=chain)
  build_openvpn_network(
   chain_ctl=chain_ctl, chain=chain, srv_node=srv_node, need_port_forwarding=need_port_forwarding,
  )
  return

 chain_ctl.execute_chain_building()
 if chain.has_proxies_chain:
  chain_ctl.build_proxies_chain(srv_node=srv_node, need_port_forwarding=need_port_forwarding)


# noinspection PyIncorrectDocstring,PyUnusedLocal
@app.task(bind=True)
def check_worker_status(
  self,
  task_identifier: str,
  is_internal=True,
  queue_name=settings.INTERNAL_CELERY_QUEUE_NAME
):
 """
 Проверка воркеров осуществляется для цепей со статусами "Готов" и "Воркер не отвечает",
 статусы получаем из api Flower'a.

 :param task_identifier: Идентификатор задачи
 :param queue_name: имя используемой очереди (`settings.INTERNAL_CELERY_QUEUE_NAME`, если is_internal=True)
 :param is_internal: если значение истина, то задача
      отправится в `settings.INTERNAL_CELERY_QUEUE_NAME`
      очередь (вопреки chain.task_queue_name)
 """
 logger.info(f'[{task_identifier}]: started')

 chains = Chain.objects.filter(
  Q(status=Chain.StatusChoice.READY) | Q(status=Chain.StatusChoice.WORKER_DONT_RESPONSE))

 if not chains:
  return

 flower_api = FlowerApi()
 online_workers = flower_api.get_dashboard_workers()
 online_queue_names = {online_worker.get('hostname').split('@')[1] for online_worker in online_workers}

 for chain in chains:
  if chain.task_queue_name not in online_queue_names:
   worker_status = Chain.StatusChoice.WORKER_DONT_RESPONSE
   log_level = Notification.LogLevelChoice.COLOR_DANGER
   Notification.send_to_all(
    content=f'На цепочке {chain.title} {worker_status.label}',
    log_level=log_level,
   )
  else:
   worker_status = Chain.StatusChoice.READY
   log_level = Notification.LogLevelChoice.COLOR_SUCCESS

  chain.status = worker_status
  chain.save(update_fields=['status'])
  logger.info(f'Changed status for {chain=} to {worker_status}')

 logger.info(f'[{task_identifier}]: finished')


# noinspection PyIncorrectDocstring,PyUnusedLocal
@app.task(bind=True)
def periodic_task_rebuild_unresponsive_workers(
  self,
  task_identifier: str,
  is_internal=True,
  queue_name=settings.INTERNAL_CELERY_QUEUE_NAME
):
 """
 Перестраивает цепочки с упавшими воркерами.

 :param task_identifier: Идентификатор задачи
 :param queue_name: имя используемой очереди (`settings.INTERNAL_CELERY_QUEUE_NAME`, если is_internal=True)
 :param is_internal: если значение истина, то задача
      отправится в `settings.INTERNAL_CELERY_QUEUE_NAME`
      очередь (вопреки chain.task_queue_name)
 """
 chains = Chain.objects.filter(status=Chain.StatusChoice.WORKER_DONT_RESPONSE)
 logger.info(f'[{task_identifier}]: started')
 for chain in chains:
  rebuild_connection.delay(
   chain_id=chain.id, is_internal=True,
   task_identifier=f'{task_identifier}:{chain.id}',
  )
 if not chains:
  logger.info(f'[{task_identifier}]: unresponsive workers not found.')


# noinspection PyIncorrectDocstring,PyUnusedLocal
@app.task(bind=True)
def generate_keys_for_chain(
  self,
  chain_id: int,
  task_identifier: str,
  is_internal=True,
  queue_name=settings.INTERNAL_CELERY_QUEUE_NAME
):
 """
 Запускает задачу генерации ssh ключей для указаного образа приложения

 :param chain_id: id цепоки, для которого генерятся ключи
 :param task_identifier: Идентификатор задачи
 :param queue_name: имя используемой очереди (`settings.INTERNAL_CELERY_QUEUE_NAME`, если is_internal=True)
 :param is_internal: если значение истина, то задача
      отправится в `settings.INTERNAL_CELERY_QUEUE_NAME`
      очередь (вопреки chain.task_queue_name)
 """
 # todo: больше логиования
 chain = Chain.objects.get(id=chain_id)
 private_key_path, public_key_path = CmdCtl.generate_ssh_keys()
 chain.openssh_container_id_rsa = str(private_key_path)
 chain.openssh_container_id_rsa_pub = str(public_key_path)
 chain.save(update_fields=['openssh_container_id_rsa', 'openssh_container_id_rsa_pub'])


# noinspection PyIncorrectDocstring,PyUnusedLocal
@app.task(bind=True)
def generate_keys_for_node(
  self,
  node_id: int,
  task_identifier: str,
  is_internal=True,
  queue_name=settings.INTERNAL_CELERY_QUEUE_NAME
):
 """
 Запускает задачу генерации ssh ключей для указаной ноды

 :param node_id: id ноды для которой генерятся ключи
 :param task_identifier: Идентификатор задачи
 :param queue_name: имя используемой очереди (`settings.INTERNAL_CELERY_QUEUE_NAME`, если is_internal=True)
 :param is_internal: если значение истина, то задача
      отправится в `settings.INTERNAL_CELERY_QUEUE_NAME`
      очередь (вопреки chain.task_queue_name)
 """
 # todo: больше логиования
 node = Node.objects.get(id=node_id)
 private_key_path, public_key_path = CmdCtl.generate_ssh_keys()
 node.id_rsa = str(private_key_path)
 node.id_rsa_pub = str(public_key_path)
 node.save(update_fields=['id_rsa', 'id_rsa_pub'])


# noinspection PyIncorrectDocstring,PyUnusedLocal
@app.task(bind=True)
def kill_processes(
  self, chain_id, task_identifier: str,
  is_internal=True, queue_name=settings.INTERNAL_CELERY_QUEUE_NAME
):
 logger.info(f'Kill processes started [{task_identifier}][{chain_id}]')
 chain = Chain.objects.get(id=chain_id)
 chain_ctl = ChainCtl(chain)
 try:
  chain_ctl.kill_connection_proc().run()
 except Exception as exc:
  logger.error(f'Kill processes failed [{task_identifier}][{chain_id}]{exc}')
  raise exc
 logger.info(f'Kill processes finished [{task_identifier}][{chain_id}]')


# noinspection PyIncorrectDocstring,PyUnusedLocal
@app.task
def rebuild_connection(_, chain_id, *args, **kwargs):
 """
 Запускает задачу перестройки соединения (туннель + проброс портов)

 :param _: Результат предыдущей задачи
 :param chain_id: id цепочки анонимизации
 """
 logger.info(f'[rebuild_connection]: start')
 anon_chain = Chain.objects.get(id=chain_id)
 chain_ctl = ChainCtl(anon_chain)
 results = chain_ctl.execute_rebuild_connection()


# noinspection PyIncorrectDocstring,PyUnusedLocal
@app.task(bind=True)
def rebuild_connection_ovpn(self, chain_id, task_identifier: str, is_internal=True):
 """
 Запускает задачу перестройки openvpn config на удаленном узле

 :param chain_id: id цепочки анонимизации
 :param task_identifier: Идентификатор задачи
 :param is_internal: если значение истина, то задача
      отправится в `settings.INTERNAL_CELERY_QUEUE_NAME`
      очередь (вопреки chain.task_queue_name)
 """

 logger.info(f'[{task_identifier}]: start rebuilding openvpn config')

 anon_chain = Chain.objects.get(id=chain_id)
 chain_ctl = ChainCtl(anon_chain)
 anon_chain.openvpn_config = ''
 anon_chain.save(update_fields=['status', 'openvpn_config', ])

 srv_node, need_port_forwarding = check_nodes_quantity(anon_chain)

 try:
  OpenVPNCtl.kill_all_containers(chain=anon_chain,
           srv_node=srv_node,
           need_port_forwarding=need_port_forwarding
           )
  build_openvpn_network(chain_ctl=chain_ctl,
        chain=anon_chain,
        srv_node=srv_node,
        need_port_forwarding=need_port_forwarding
        )
 except Exception as exc:
  anon_chain.status = Chain.StatusChoice.CREATING_FAILED
  anon_chain.save(update_fields=['status'])

  Notification.send_to_all(
   content=f'Цепочка {anon_chain.title}, упала при перестроении с ошибкой',
   log_level=Notification.LogLevelChoice.COLOR_DANGER.value,
   error=f'{exc}',
   traceback=traceback.format_exc(),
  )

  logger.error(f'[{task_identifier}]: `{exc.__class__.__name__}: {exc}`')
  chain_ctl.kill_connection_proc().run(raise_exc=False)

  raise exc


# noinspection PyIncorrectDocstring,PyUnusedLocal
@app.task(bind=True)
def rebuild_proxychains4(self, chain_id, proxies, task_identifier: str, is_internal=True):
 """Start task to rebuild proxychains4 with new proxies on a remote node.

 :param chain_id: id цепочки анонимизации.
 :param proxies: Прокси сервера для генерации конфигурационного файла.
 :param task_identifier: Идентификатор задачи.
 :param is_internal: если значение истина, то задача
      отправится в `settings.INTERNAL_CELERY_QUEUE_NAME`
      очередь (вопреки chain.task_queue_name)
 """
 logger.info(f'[{task_identifier}]: start')
 anon_chain = Chain.objects.get(id=chain_id)
 anon_chain.status = Chain.StatusChoice.REBUILD_CONNECTION
 anon_chain.save(update_fields=['status'])

 chain_ctl = ChainCtl(anon_chain)
 try:
  srv_node, need_port_forwarding = check_nodes_quantity(anon_chain)
  chain_ctl.generate_proxychains4_config(
   proxies=proxies, srv_node=srv_node, is_forwarded=need_port_forwarding,
  )
  chain_ctl.build_proxies_chain(srv_node=srv_node, need_port_forwarding=need_port_forwarding)
  anon_chain.status = Chain.StatusChoice.READY
  anon_chain.save(update_fields=['status'])
  logger.info(f'[{task_identifier}]: success')
  Notification.send_to_all(
   content=f'Цепочка {anon_chain.title} с использованием цепочки из прокси серверов была успешно перестроена',
   log_level=Notification.LogLevelChoice.COLOR_SUCCESS.value
  )
 except Exception as exception:
  anon_chain.status = Chain.StatusChoice.CREATING_FAILED
  anon_chain.save(update_fields=['status'])
  Notification.send_to_all(
   content=f'Цепочка {anon_chain.title}, упала при {task_identifier} с ошибкой',
   log_level=Notification.LogLevelChoice.COLOR_DANGER.value,
   error=f'{exception}',
   traceback=traceback.format_exc(),
  )
  logger.error(f'[{task_identifier}]: `{exception.__class__.__name__}: {exception}`')
  chain_ctl.kill_connection_proc().run(raise_exc=False)
  raise exception


# noinspection PyIncorrectDocstring,PyUnusedLocal
@app.task
def rebuild_chain_with_reload_img(_, chain_id: int, *args, **kwargs):
 """
 Запускает задачу перестройки цепочки с загрузкой нового образа

 :param _: Результат предыдущей задачи
 :param chain_id: id цепочки анонимизации
 """
 logger.info(f'[rebuild_chain_with_reload_img]: start')
 anon_chain = Chain.objects.get(id=chain_id)
 chain_ctl = ChainCtl(anon_chain)
 chain_ctl.execute_chain_building()


@app.task(bind=True)
def share_private_network_via_ovpn(self, node_id, task_identifier: str, is_internal=True):
 logger.info(f'Start building ovpn connect for private network [{task_identifier}]')
 node = Node.objects.get(id=node_id)
 try:
  OpenVPNCtl.build4private_network(srv_node=node)
  conf, _ = OpenVPNCtl.add_client(srv_node=node, is_forwarded=False)
  logger.info(
   f'ovpn connection for private network was built, '
   f'OpenVPNClient config: {conf} [{task_identifier}]'
  )
  Notification.send_to_all(
   content=f'Впн был успешно построен на {node}',
   log_level=Notification.LogLevelChoice.COLOR_SUCCESS.value
  )
 except Exception as e:
  Notification.send_to_all(
   content=f'При построении впн на {node}, произошла ошибка',
   log_level=Notification.LogLevelChoice.COLOR_DANGER.value,
   error=f'{e}',
   traceback=traceback.format_exc(),
  )
  raise
 return f'Client config id: {conf.id}'


@app.task
def reconnect_private_network_task(node_id, *args, **kwargs):
 """Восстановить подключение к внутренней сети с существующими конфигурационными файлами OpenVPN
 после перезапуска контейнеров.

 :param node_id: ID узла цепочки анонимизации
 """
 node = Node.objects.get(id=node_id)
 OpenVPNCtl.reconnect_private_network(srv_node=node)
 return node_id


@app.task
def successful_reconnect_private_network(node_id: int, *args, **kwargs):
 """Информирование пользователей об успешном переподключении к внутренней сети.

 :param node_id: ID узла цепочки анонимизации
 """
 node = Node.objects.get(id=node_id)
 Notification.send_to_all(
  content=f'Перестроение соединения к внутренней сети для узла "{node}" завершилось успешно.',
  log_level=Notification.LogLevelChoice.COLOR_SUCCESS.value,
 )
 logger.info(f'Rebuild private network connection for "{node}" was completed successfully.')


@app.task
def callback_reconnect_private_network_errors(request, exc, traceback, node_id: int):
 """Выполнение ряда процедур в случае ошибки при перестроении соединения к внутренней сети.

 :param request: Контекст цепочки задач
 :param exc: Экземпляр класса Exception, вызванный в процессе выполнения цепочки задач
 :param traceback: Трейсбэк ошибки, вызванной в процессе выполнения цепочки задач
 :param node_id: ID узла цепочки анонимизации
 """
 node = Node.objects.get(id=node_id)
 Notification.send_to_all(
  content=f'Перестроение соединения к внутренней сети для узла "{node}" завершилось с ошибкой.',
  log_level=Notification.LogLevelChoice.COLOR_DANGER.value,
  error=f'{exc}',
  traceback=traceback,
 )
 logger.error(exc)


# TODO: wtfovpn
@app.task(bind=True)
def add_client4ovpn_server(self, ovpn_conf_id, task_identifier: str, is_internal=True):
 ovpn_conf = OpenVPNClient.objects.get(id=ovpn_conf_id)
 logger.info(f'Try to add new ovpn client for {ovpn_conf.node} [{task_identifier}]')

 edges = Edge.objects.filter(Q(out_node=ovpn_conf.node) | Q(in_node=ovpn_conf.node))
 is_forwarded = edges.exists()
 conf = None

 try:
  conf, _ = OpenVPNCtl.add_client(ovpn_conf=ovpn_conf, is_forwarded=is_forwarded)
 except CmdError as e:
  if not any(edge.chain.status == Chain.StatusChoice.READY for edge in edges):
   logger.warning(
    f'Cann\'t add_client4ovpn_server maybe because '
    f'forwarded node lost connection [{task_identifier}]'
   )
  raise e

 logger.info(
  f'OpenVPNClient config was created: {conf} [{task_identifier}]'
 )
 return f'Client config id: {conf.id}'


try:
 from manage_app.tasks.celery_tasks_classes import ReRaiseTask
 baseClass = ReRaiseTask
except ModuleNotFoundError:
 baseClass = None


@app.task(base=baseClass)
def async_are_proxies_alive(*args, **kwargs):
 """
 Функция получает используемые в данный момент прокси на цепочке. Далее передает их на проверку в прокси чекер.
 """
 check_url = 'https://www.example.com/'
 if args:
  proxies = args[0]
 else:
  proxies = kwargs['proxies']
 proxy_checker = ProxyChecker(proxies=proxies)
 return proxy_checker.check_state(url=check_url)


@app.task(base=baseClass)
def get_updated_alive_proxies(proxies: List[dict], chain_id: int, *args, **kwargs) -> List[dict]:
 """Update Proxy model state in database and return list of alive proxies.

 Args:
  proxies: list of dict serialized proxy models. Proxy dicts must contain 'is_dead' field
    with boolean value
  chain_id: Chain ID

 Returns:
  list of proxies with a state field equal to 'ALIVE'
 """
 alive_proxies = [proxy for proxy in proxies if not proxy['check']['is_dead']]
 if not alive_proxies:
  chain = Chain.objects.get(id=chain_id)
  raise ChainHasNoAliveProxies(chain=chain)
 return alive_proxies


@app.task
def check_proxy_location(proxies: List[dict], check_location_url, *args, **kwargs):
 proxy_checker = ProxyChecker(proxies=proxies)
 return proxy_checker.check_location(check_location_url)


@app.task
def set_proxies_state(proxies: List[dict], state: str, *args, **kwargs):
 ids = (proxy['pk'] for proxy in proxies)
 Proxy.objects.filter(id__in=ids).update(state=state)
 logger.info('Proxy states have been changed.')
 return proxies


@app.task
def update_proxies(proxies: List[dict], *args, **kwargs):
 proxy_objs = (proxy.object for proxy in serializers.deserialize('json', json.dumps(proxies)))
 Proxy.objects.bulk_update(proxy_objs, fields=('state', 'location', 'last_check_dt', 'last_successful_check_dt'))


@app.task(base=QueueOnce, once={'graceful': True})
def periodic_task_for_check_proxies(*args, **kwargs):
 logger.info(f'{periodic_task_for_check_proxies.__name__} is starting')
 chains = Chain.objects.filter(status=Chain.StatusChoice.READY)
 proxies = Proxy.objects.exclude(applying=Proxy.ApplyingChoice.BLACKLIST)
 if not chains.exists():
  logger.error(f'{periodic_task_for_check_proxies.__name__} failed. No chains alive.')
  return
 if not proxies.exists():
  logger.info(f'{periodic_task_for_check_proxies.__name__} completed. There are no proxies to check.')
  return
 test_chain_id = random.choice(chains.values_list('pk', flat=True))
 test_chain = Chain.objects.get(pk=test_chain_id)
 serialized_proxies = json.loads(serializers.serialize('json', proxies))
 tasks_chain = test_chain.create_tasks_chain_for_proxies(serialized_proxies, check_proxies_location=False)
 tasks_chain.apply_async()


@app.task(bind=True)
def check_chain_status(self, chain_id, task_identifier: str, is_internal=True):
 logger.info(f'start check_chain_status [{task_identifier}]')
 chain = Chain.objects.get(id=chain_id)

 try:
  rtt = CmdCtl.get_port_rtt('localhost', chain.exit_node.ssh_proc_port)
  upload_speed, download_speed = CmdCtl.get_ssh_connection_speed(
   chain.exit_node, is_forwarded_target=True
  )
  ports_status = CmdCtl.get_chain_ports_status(chain.exit_node)

  chain.ports_info = ports_status
  chain.upload_speed = upload_speed
  chain.download_speed = download_speed
  chain.ping = rtt
  failed = any(state != 'open' for state in ports_status.values())
  sorted_edges = chain.sorted_edges
  for index, edge in enumerate(sorted_edges):
   is_forwarded_src = index != 0
   if len(sorted_edges) == 1:
    break
   rtt = CmdCtl.get_port_rtt(
    target_host=edge.in_node.server.ssh_ip,
    target_port=edge.in_node.server.ssh_port,
    host=edge.out_node, is_forwarded=is_forwarded_src
   )
   upload_speed, download_speed = CmdCtl.get_ssh_connection_speed(
    target_node=edge.out_node, host=edge.in_node,
    is_forwarded_target=False, is_forwarded_src=False
   )

   edge.ping = rtt
   edge.upload_speed = upload_speed
   edge.download_speed = download_speed
   edge.save(update_fields=['ping', 'upload_speed', 'download_speed'])
  Notification.send_to_all(
   content=f'Тестирование цепочки {chain.title}, завершилось с успешно',
   log_level=Notification.LogLevelChoice.COLOR_SUCCESS.value
  )
 except Exception as e:
  Notification.send_to_all(
   content=f'Тестирование цепочки {chain.title}, завершилось с ошибкой',
   log_level=Notification.LogLevelChoice.COLOR_DANGER.value,
   error=f'{e}',
   traceback=traceback.format_exc(),
  )
  logger.error(e, exc_info=True)
  failed = True

 chain.status = Chain.StatusChoice.DIED if failed else Chain.StatusChoice.READY
 chain.last_update_info_dt = datetime.datetime.now()

 chain.save(
  update_fields=[
   'ports_info', 'upload_speed', 'download_speed',
   'ping', 'status', 'last_update_info_dt'
  ]
 )

 return chain.status.value