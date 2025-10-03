import os
import time

import random
from django.shortcuts import reverse
from factory import Faker, django as django_factory
from faker import Faker
from faker.providers import internet

from anon_app.conf import settings
from anon_app.factories import AppImageModelFactory
from anon_app.models import Hosting, Server, Node, Edge, SrvAccount
from soi_app.settings import MEDIA_ROOT


def get_new_hosting_data():
 result = {
  'name': 'test-hosting-name',
  'url': f'http://test{random.randint(0, 10000)}.org'
 }
 return result, {**result}


def get_new_hosting_account_data():
 src_result = {
  'username': f'user-{random.randint(0, 10000)}',
  'password': f'password-{random.randint(0, 10000)}',
  'hosting': Hosting.objects.create(**get_new_hosting_data()[0])
 }
 hyperlinked_result = {
  **src_result,
  'hosting': f"https://{settings.ANON_APP_HOST.strip('/')}/"
      f"{reverse('hosting-list').strip('/')}/{src_result['hosting'].id}/?format=json"
 }
 return src_result, hyperlinked_result


def get_new_server_data(**kwargs):
 fake = Faker()
 fake.add_provider(internet)
 src_result = {
  'hosting': Hosting.objects.create(**get_new_hosting_data()[0]),
  'ssh_ip': fake.ipv4(),
  'ssh_port': random.randint(1025, 65535),
  'geo': random.choice(['RU', 'SE', 'DE', 'KZ', 'US']),
  **kwargs
 }
 hyperlinked_result = {
  **src_result,
  'hosting': f"https://{settings.ANON_APP_HOST.strip('/')}/"
      f"{reverse('hosting-list').strip('/')}/{src_result['hosting'].id}"
      f"/?format=json"
 }

 return src_result, hyperlinked_result


def get_new_server_account_data(**kwargs):
 src_result = {
  'username': f'user-{random.randint(0, 10000)}',
  'password': f'password-{random.randint(0, 10000)}',
  'server': Server.objects.create(**get_new_server_data()[0]),
  **kwargs
 }

 hyperlinked_result = {
  **src_result,
  'server': f"https://{settings.ANON_APP_HOST.strip('/')}/"
     f"{reverse('server-list').strip('/')}/{src_result['server'].id}/?format=json"
 }
 return src_result, hyperlinked_result


def get_new_node_data(node_files=None, **kwargs):
 node_files = {} if node_files is None else node_files

 src_result = {
  'server': Server.objects.create(**get_new_server_data()[0]),
  'ssh_proc_port': random.randint(1025, 65535),
  **node_files
 }

 SrvAccount.objects.create(
  **get_new_server_account_data(server=src_result['server'])[0]
 )

 hyperlinked_result = {
  **src_result,
  'server': f"https://{settings.ANON_APP_HOST.strip('/')}/"
     f"{reverse('server-list').strip('/')}/{src_result['server'].id}/?format=json"
 }
 return src_result, hyperlinked_result


def get_new_edges_data(node_count, **kwargs):
 src_nodes = [Node.objects.create(**get_new_node_data(**kwargs)[0]) for _ in range(node_count)]
 hyperlinked_nodes = [
  f"https://{settings.ANON_APP_HOST}/{reverse('node-list').strip('/')}/{n.id}/?format=json" for n in src_nodes
 ]
 src_result, hyperlinked_result = [], []
 for i in range(node_count - 1):
  protocol = random.choice([p[0] for p in Edge.ProtocolChoice.choices]) \
   if i != 0 else Edge.ProtocolChoice.SSH.value
  src_result.append({
   'out_node': src_nodes[i],
   'in_node': src_nodes[i + 1],
   'protocol': protocol,
  })
  hyperlinked_result.append({
   'out_node': hyperlinked_nodes[i],
   'in_node': hyperlinked_nodes[i + 1],
   'protocol': protocol,
  })
 return src_result, hyperlinked_result


def get_new_srv_edges_data(node_count, **kwargs):
 src_nodes = [Server.objects.create(**get_new_server_data(**kwargs)[0]) for _ in range(node_count)]
 hyperlinked_nodes = [
  f"https://{settings.ANON_APP_HOST}/{reverse('server-list').strip('/')}/{n.id}/?format=json" for n in src_nodes
 ]

 src_result, hyperlinked_result = [], []

 for i in range(node_count - 1):
  protocol = random.choice([p[0] for p in Edge.ProtocolChoice.choices]) \
   if i != 0 else Edge.ProtocolChoice.SSH.value
  src_result.append({
   'out_node': src_nodes[i],
   'in_node': src_nodes[i + 1],
   'protocol': protocol,
  })
  hyperlinked_result.append({
   'out_node': hyperlinked_nodes[i],
   'in_node': hyperlinked_nodes[i + 1],
   'protocol': protocol,
  })
 return src_result, hyperlinked_result


def get_new_chain_data(**init_data):
 task_queue_name = init_data['task_queue_name'] \
  if init_data.get('task_queue_name') is not None \
  else f'test_queue_{time.time()}'
 src_edges, hyperlinked_edges = get_new_edges_data(settings.ANON_APP_MIN_CHAIN_SIZE, **init_data)
 app_image = AppImageModelFactory()

 # openssh_container_id_rsa = os.path.join(MEDIA_ROOT, 'id_rsa')
 # openssh_container_id_rsa_pub = os.path.join(MEDIA_ROOT, 'id_rsa.pub')
 openssh_container_external_port = 6996
 openssh_container_internal_port = 9669

 src_result = {
  'title': f'{random.randint(0, 10000)}-chain',
  'task_queue_name': task_queue_name,
  'edges': src_edges,
  'app_image': app_image,
  # 'openssh_container_id_rsa': openssh_container_id_rsa,
  # 'openssh_container_id_rsa_pub': openssh_container_id_rsa_pub,
  'openssh_container_external_port': openssh_container_external_port,
  'openssh_container_internal_port': openssh_container_internal_port
 }
 hyperlinked_result = {
  **src_result,
  'edges': hyperlinked_edges,
  'app_image': f"https://{settings.ANON_APP_HOST}/{reverse('appimage-list').strip('/')}/{app_image.id}/?format=json"
 }
 return src_result, hyperlinked_result