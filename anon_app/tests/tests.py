import json
import logging
import random
from pathlib import Path

import factory
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.shortcuts import reverse
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APITestCase, APIRequestFactory, force_authenticate

from anon_app.conf import settings
from anon_app.factories import (
 ServerModelFactory, HostingModelFactory, HostingAccountModelFactory, ServerAccountModelFactory, ProxyModelFactory)
from anon_app.forms import ChainAdminForm
from anon_app.models import Chain, Edge, Node, Server, Hosting, HostingAccount, SrvAccount, Proxy
from anon_app.serializers import ServerSerializer
from anon_app.tests.datasource import get_new_chain_data, get_new_node_data, get_new_srv_edges_data
from anon_app.tests.mixins import (
 ModelViewActionsTestMixin, ModelViewDestroyTestMixin, ModelViewListTestMixin,
 ModelViewRetrieveTestMixin, ModelViewUpdateTestMixin, ModelViewPartialUpdateTestMixin)
from anon_app.utils import create_test_users
from anon_app.views import ServerView, HostingView, HostingAccountView, ServerAccountView, ProxyView
from soi_app.settings import DATA_PREFIX

logging.basicConfig(level=logging.INFO)


class ChainViewTest(ModelViewActionsTestMixin, TestCase):
 is_logined = False
 need_admin_user = True
 model = Chain
 data_generator = get_new_chain_data
 view_url_name = 'chain-list'
 update_data_generator = lambda: {'title': f'{random.randint(0, 10000)}-chain'}

 def test_create(self):
  _, srv_edges = get_new_srv_edges_data(settings.ANON_APP_MIN_CHAIN_SIZE)
  _, chain_data = self.__class__.data_generator()

  data = {**chain_data, 'edges': srv_edges}

  resp = self.client.post(
   reverse(self.view_url_name),
   data=json.dumps(data, ensure_ascii=False),
   content_type='application/json'
  )
  msg = f"resp: {resp.json()} | data: {self.last_instance_hyperlinked_data}"
  self.assertEqual(resp.status_code, 201, msg=msg)

 @classmethod
 def setUpClass(cls):
  super(ChainViewTest, cls).setUpClass()
  create_test_users()

 def setUp(self) -> None:
  self.last_instance_src_data, self.last_instance_hyperlinked_data = get_new_chain_data()

  edges = self.last_instance_src_data.pop('edges')
  self.last_instance = Chain.objects.create(**self.last_instance_src_data)

  for edge in edges:
   Edge.objects.create(**edge, chain=self.last_instance)

  if not self.is_logined:
   self.client.login(
    username=settings.ANON_APP_TEST_USER_NAME if not self.need_admin_user
    else settings.ANON_APP_TEST_SUPERUSER_NAME,
    password=settings.ANON_APP_TEST_USER_PASSWORD if not self.need_admin_user
    else settings.ANON_APP_TEST_SUPERUSER_PASSWORD
   )
   self.is_logined = True


class NodeViewTest(ModelViewActionsTestMixin, TestCase):
 is_logined = False
 need_admin_user = True
 model = Node
 view_url_name = 'node-list'
 data_generator = get_new_node_data
 update_data_generator = lambda: {'geo': f'{random.random() * 100}:{random.random() * 100}'}

 @classmethod
 def setUpClass(cls):
  super(NodeViewTest, cls).setUpClass()
  create_test_users()


class EdgeViewTest(
 ModelViewDestroyTestMixin,
 ModelViewListTestMixin,
 ModelViewRetrieveTestMixin,
 ModelViewUpdateTestMixin,
 ModelViewPartialUpdateTestMixin,
 TestCase
):
 is_logined = False
 need_admin_user = True
 model = Edge
 view_url_name = 'edge-list'

 @staticmethod
 def update_data_generator():
  node = Node.objects.create(**get_new_node_data()[0])
  new_node = f"https://{settings.ANON_APP_HOST}/{reverse('node-list').strip('/')}/{node.id}/?format=json"
  return {
   'out_node': new_node
  }

 @classmethod
 def setUpClass(cls):
  super(EdgeViewTest, cls).setUpClass()
  create_test_users()

 def setUp(self) -> None:
  chain_src_data, chain_hyperlinked_data = get_new_chain_data()
  self.last_instance_src_data = chain_src_data.pop('edges')[0]
  self.last_instance_hyperlinked_data = chain_hyperlinked_data['edges'][0]
  chain = Chain.objects.create(**chain_src_data)
  self.last_instance = Edge.objects.create(**self.last_instance_src_data, chain=chain)

  if not self.is_logined:
   self.client.login(
    username=settings.ANON_APP_TEST_USER_NAME if not self.need_admin_user
    else settings.ANON_APP_TEST_SUPERUSER_NAME,
    password=settings.ANON_APP_TEST_USER_PASSWORD if not self.need_admin_user
    else settings.ANON_APP_TEST_SUPERUSER_PASSWORD
   )
   self.is_logined = True


class HostingViewTestDRF(APITestCase):
 fixtures = ['anon_fixture.json']
 entry_point = 'hosting'

 def setUp(self):
  self.user = User.objects.last()
  self.request_factory = APIRequestFactory()

 def test_list(self):
  request = self.request_factory.get(self.entry_point)
  force_authenticate(request, user=self.user)
  srv_view = HostingView.as_view({'get': 'list'})
  response = srv_view(request)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')

 def test_create(self):
  hosting_data = factory.build(dict, FACTORY_CLASS=HostingModelFactory)
  request = self.request_factory.post(self.entry_point, hosting_data, format='json')
  force_authenticate(request, user=self.user)
  hosting_view = HostingView.as_view({'post': 'create'})
  response = hosting_view(request)
  self.assertEqual(response.status_code, status.HTTP_201_CREATED, msg=f'Response: {response.data}')

 def test_read(self):
  hosting = Hosting.objects.first()
  request = self.request_factory.get(self.entry_point)
  force_authenticate(request, user=self.user)
  hosting_view = HostingView.as_view({'get': 'retrieve'})
  response = hosting_view(request, pk=hosting.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertEqual(response.data['name'], hosting.name)

 def test_update(self):
  hosting = Hosting.objects.first()
  new_hosting = HostingModelFactory.build()
  new_hosting_data = {
   'name': new_hosting.name,
   'url': new_hosting.url,
  }
  request = self.request_factory.put(self.entry_point, new_hosting_data, format='json')
  force_authenticate(request, user=self.user)
  ir_view = HostingView.as_view({'put': 'update'})
  response = ir_view(request, pk=hosting.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertNotEqual(response.data['name'], hosting.name)
  self.assertNotEqual(response.data['url'], hosting.url)
  self.assertEqual(response.data['name'], new_hosting_data['name'])

 def test_partial_update(self):
  hosting = Hosting.objects.first()
  new_hosting = HostingModelFactory.build()
  new_hosting_data = {
   'name': new_hosting.name,
  }
  request = self.request_factory.patch(self.entry_point, new_hosting_data, format='json')
  force_authenticate(request, user=self.user)
  hosting_view = HostingView.as_view({'patch': 'partial_update'})
  response = hosting_view(request, pk=hosting.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertNotEqual(response.data['name'], hosting.name)
  self.assertEqual(response.data['url'], hosting.url)

 def test_delete(self):
  hosting = Hosting.objects.first()
  request = self.request_factory.delete(self.entry_point)
  force_authenticate(request, user=self.user)
  hosting_view = HostingView.as_view({'delete': 'destroy'})
  response = hosting_view(request, pk=hosting.pk)
  self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT, msg=f'Response: {response.data}')


class ServerViewTestDRF(APITestCase):
 fixtures = ['anon_fixture.json']
 entry_point = 'server'

 def setUp(self):
  self.user = User.objects.last()
  self.request_factory = APIRequestFactory()

 def test_list(self):
  request = self.request_factory.get(self.entry_point)
  force_authenticate(request, user=self.user)
  srv_view = ServerView.as_view({'get': 'list'})
  response = srv_view(request)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')

 def test_create(self):
  server = factory.build(dict, FACTORY_CLASS=ServerModelFactory)
  server['hosting'].save()
  server['hosting'] = reverse('hosting-detail', kwargs={'pk': server['hosting'].pk})
  request = self.request_factory.post(self.entry_point, server, format='json')
  force_authenticate(request, user=self.user)
  server_view = ServerView.as_view({'post': 'create'})
  response = server_view(request)
  self.assertEqual(response.status_code, status.HTTP_201_CREATED, msg=f'Response: {response.data}')

 def test_read(self):
  server = Server.objects.first()
  request = self.request_factory.get(self.entry_point)
  force_authenticate(request, user=self.user)
  server_view = ServerView.as_view({'get': 'retrieve'})
  response = server_view(request, pk=server.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertEqual(response.data['ssh_ip'], server.ssh_ip)

 def test_update(self):
  server = Server.objects.first()

  new_server = factory.build(dict, FACTORY_CLASS=ServerModelFactory)
  new_server['hosting'].save()
  new_server['hosting'] = reverse('hosting-detail', kwargs={'pk': new_server['hosting'].pk})

  new_server_data = {
   'hosting': new_server['hosting'],
   'ssh_ip': new_server['ssh_ip'],
  }

  request = self.request_factory.put(self.entry_point, new_server_data, format='json')
  force_authenticate(request, user=self.user)
  server_view = ServerView.as_view({'put': 'update'})
  response = server_view(request, pk=server.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertNotEqual(response.data['hosting'], server.hosting)
  self.assertNotEqual(response.data['ssh_ip'], server.ssh_ip)

 def test_partial_update(self):
  server = Server.objects.first()

  new_server = factory.build(dict, FACTORY_CLASS=ServerModelFactory)

  new_server_data = {
   'ssh_ip': new_server['ssh_ip'],
  }

  request = self.request_factory.patch(self.entry_point, new_server_data, format='json')
  force_authenticate(request, user=self.user)
  server_view = ServerView.as_view({'patch': 'partial_update'})
  response = server_view(request, pk=server.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertNotEqual(response.data['ssh_ip'], server.ssh_ip)

 def test_delete(self):
  server = Server.objects.first()
  request = self.request_factory.delete(self.entry_point)
  force_authenticate(request, user=self.user)
  server_view = ServerView.as_view({'delete': 'destroy'})
  response = server_view(request, pk=server.pk)
  self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT, msg=f'Response: {response.data}')


class HostingAccountTestDRF(APITestCase):
 fixtures = ['anon_fixture.json']
 entry_point = 'hosting_account'

 def setUp(self):
  self.user = User.objects.last()
  self.request_factory = APIRequestFactory()

 def test_list(self):
  request = self.request_factory.get(self.entry_point)
  force_authenticate(request, user=self.user)
  hosting_account_view = HostingAccountView.as_view({'get': 'list'})
  response = hosting_account_view(request)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')

 def test_create(self):
  hosting_account = factory.build(dict, FACTORY_CLASS=HostingAccountModelFactory)
  hosting_account['hosting'].save()
  hosting_account['hosting'] = reverse('hosting-detail', kwargs={'pk': hosting_account['hosting'].pk})
  request = self.request_factory.post(self.entry_point, hosting_account, format='json')
  force_authenticate(request, user=self.user)
  hosting_account_view = HostingAccountView.as_view({'post': 'create'})
  response = hosting_account_view(request)
  self.assertEqual(response.status_code, status.HTTP_201_CREATED, msg=f'Response: {response.data}')

 def test_read(self):
  hosting_account = HostingAccount.objects.first()
  request = self.request_factory.get(self.entry_point)
  force_authenticate(request, user=self.user)
  hosting_account_view = HostingAccountView.as_view({'get': 'retrieve'})
  response = hosting_account_view(request, pk=hosting_account.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertEqual(response.data['username'], hosting_account.username)
  self.assertEqual(response.data['password'], hosting_account.password)

 def test_update(self):
  hosting_account = HostingAccount.objects.first()
  new_hosting_account = factory.build(dict, FACTORY_CLASS=HostingAccountModelFactory)
  new_hosting_account['hosting'].save()
  new_hosting_account['hosting'] = reverse('hosting-detail', kwargs={'pk': new_hosting_account['hosting'].pk})
  new_hosting_account_data = {
   'hosting': new_hosting_account['hosting'],
   'username': new_hosting_account['username'],
   'password': new_hosting_account['password'],
  }
  request = self.request_factory.put(self.entry_point, new_hosting_account_data, format='json')
  force_authenticate(request, user=self.user)
  hosting_account_vew = HostingAccountView.as_view({'put': 'update'})
  response = hosting_account_vew(request, pk=hosting_account.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertNotEqual(response.data['hosting'], hosting_account.hosting)
  self.assertNotEqual(response.data['username'], hosting_account.username)
  self.assertNotEqual(response.data['password'], hosting_account.password)

 def test_partial_update(self):
  hosting_account = HostingAccount.objects.first()

  new_hosting_account = factory.build(dict, FACTORY_CLASS=HostingAccountModelFactory)
  new_hosting_account_data = {
   'password': new_hosting_account['password'],
  }

  request = self.request_factory.patch(self.entry_point, new_hosting_account_data, format='json')
  force_authenticate(request, user=self.user)
  hosting_account_vew = HostingAccountView.as_view({'patch': 'partial_update'})
  response = hosting_account_vew(request, pk=hosting_account.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertEqual(response.data['username'], hosting_account.username)
  self.assertNotEqual(response.data['password'], hosting_account.password)

 def test_delete(self):
  hosting_acount = HostingAccount.objects.first()
  request = self.request_factory.delete(self.entry_point)
  force_authenticate(request, user=self.user)
  hosting_account_view = HostingAccountView.as_view({'delete': 'destroy'})
  response = hosting_account_view(request, pk=hosting_acount.pk)
  self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT, msg=f'Response: {response.data}')


class ServerAccountTestDRF(APITestCase):
 fixtures = ['anon_fixture.json']
 entry_point = 'server_account'

 def setUp(self):
  self.user = User.objects.last()
  self.request_factory = APIRequestFactory()

 def test_list(self):
  request = self.request_factory.get(self.entry_point)
  force_authenticate(request, user=self.user)
  server_account_view = ServerAccountView.as_view({'get': 'list'})
  response = server_account_view(request)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')

 def test_create(self):
  server_account = factory.build(dict, FACTORY_CLASS=ServerAccountModelFactory)
  server_account['server'].hosting.save()
  server_account['server'].save()
  request = self.request_factory.get(self.entry_point)
  server_instance = ServerSerializer(instance=server_account['server'],
            context={'request': request}).data
  server_account['server'] = server_instance['url']
  request = self.request_factory.post(self.entry_point, server_account, format='json')
  force_authenticate(request, user=self.user)
  server_account_view = ServerAccountView.as_view({'post': 'create'})
  response = server_account_view(request)
  self.assertEqual(response.status_code, status.HTTP_201_CREATED, msg=f'Response: {response.data}')

 def test_read(self):
  srv_account = SrvAccount.objects.first()
  request = self.request_factory.get(self.entry_point)
  force_authenticate(request, user=self.user)
  srv_account_view = ServerAccountView.as_view({'get': 'retrieve'})
  response = srv_account_view(request, pk=srv_account.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertEqual(response.data['username'], srv_account.username)
  self.assertEqual(response.data['password'], srv_account.password)

 def test_update(self):
  srv_account = SrvAccount.objects.first()

  new_srv_account = factory.build(dict, FACTORY_CLASS=ServerAccountModelFactory)
  new_srv_account['server'].hosting.save()
  new_srv_account['server'].save()
  request = self.request_factory.get(self.entry_point)
  hosting_instance = ServerSerializer(instance=new_srv_account['server'],
           context={'request': request}).data
  new_srv_account['server'] = hosting_instance['url']

  new_hosting_account_data = {
   'server': new_srv_account['server'],
   'username': new_srv_account['username'],
   'password': new_srv_account['password'],
  }

  request = self.request_factory.put(self.entry_point, new_hosting_account_data, format='json')
  force_authenticate(request, user=self.user)
  server_account_vew = ServerAccountView.as_view({'put': 'update'})
  response = server_account_vew(request, pk=srv_account.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertNotEqual(response.data['server'], srv_account.server)
  self.assertNotEqual(response.data['username'], srv_account.username)
  self.assertNotEqual(response.data['password'], srv_account.password)

 def test_partial_update(self):
  srv_account = SrvAccount.objects.first()

  new_srv_account = factory.build(dict, FACTORY_CLASS=ServerAccountModelFactory)

  new_hosting_account_data = {
   'password': new_srv_account['password'],
  }

  request = self.request_factory.patch(self.entry_point, new_hosting_account_data, format='json')
  force_authenticate(request, user=self.user)
  server_account_vew = ServerAccountView.as_view({'patch': 'partial_update'})
  response = server_account_vew(request, pk=srv_account.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertEqual(response.data['username'], srv_account.username)
  self.assertNotEqual(response.data['password'], srv_account.password)

 def test_delete(self):
  srv_acount = SrvAccount.objects.first()
  request = self.request_factory.delete(self.entry_point)
  force_authenticate(request, user=self.user)
  srv_account_view = ServerAccountView.as_view({'delete': 'destroy'})
  response = srv_account_view(request, pk=srv_acount.pk)
  self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT, msg=f'Response: {response.data}')


class ProxyViewTestDRF(APITestCase):
 fixtures = ['anon_fixture.json']
 entry_point = 'proxy'

 def setUp(self):
  self.user = User.objects.last()
  self.request_factory = APIRequestFactory()

 def test_list(self):
  request = self.request_factory.get(self.entry_point)
  force_authenticate(request, user=self.user)
  srv_view = ProxyView.as_view({'get': 'list'})
  response = srv_view(request)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')

 def test_create(self):
  proxy_data = factory.build(dict, FACTORY_CLASS=ProxyModelFactory)
  request = self.request_factory.post(self.entry_point, proxy_data, format='json')
  force_authenticate(request, user=self.user)
  proxy_view = ProxyView.as_view({'post': 'create'})
  response = proxy_view(request)
  self.assertEqual(response.status_code, status.HTTP_201_CREATED, msg=f'Response: {response.data}')

 def test_read(self):
  proxy = Proxy.objects.first()
  request = self.request_factory.get(self.entry_point)
  force_authenticate(request, user=self.user)
  proxy_view = ProxyView.as_view({'get': 'retrieve'})
  response = proxy_view(request, pk=proxy.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertEqual(response.data['ip'], proxy.ip)

 def test_update(self):
  proxy = Proxy.objects.first()
  new_proxy = ProxyModelFactory.build()
  new_proxy_data = {
   'protocol': new_proxy.protocol,
   'username': new_proxy.username,
   'password': new_proxy.password,
   'ip': new_proxy.ip,
   'port': new_proxy.port,
   'location': new_proxy.location
  }
  request = self.request_factory.put(self.entry_point, new_proxy_data, format='json')
  force_authenticate(request, user=self.user)
  proxy_view = ProxyView.as_view({'put': 'update'})
  response = proxy_view(request, pk=proxy.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertNotEqual(response.data['username'], proxy.username)
  self.assertNotEqual(response.data['password'], proxy.password)
  self.assertEqual(response.data['password'], new_proxy_data['password'])

 def test_partial_update(self):
  proxy = Proxy.objects.first()
  new_proxy = ProxyModelFactory.build()
  new_proxy_data = {
   'username': new_proxy.username,
  }
  request = self.request_factory.patch(self.entry_point, new_proxy_data, format='json')
  force_authenticate(request, user=self.user)
  proxy_view = ProxyView.as_view({'patch': 'partial_update'})
  response = proxy_view(request, pk=proxy.pk)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')
  self.assertNotEqual(response.data['username'], proxy.username)
  self.assertEqual(response.data['ip'], proxy.ip)

 def test_delete(self):
  proxy = Proxy.objects.first()
  request = self.request_factory.delete(self.entry_point)
  force_authenticate(request, user=self.user)
  proxy_view = ProxyView.as_view({'delete': 'destroy'})
  response = proxy_view(request, pk=proxy.pk)
  self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT, msg=f'Response: {response.data}')


class TestImportProxy(TestCase):
 api_url = reverse('import-proxies')
 path_to_data = Path(DATA_PREFIX, 'anon_app', 'tests', 'import_data')

 @classmethod
 def setUpClass(cls):
  super(TestImportProxy, cls).setUpClass()
  create_test_users()

 def setUp(self) -> None:
  self.client.login(
   username=settings.ANON_APP_TEST_SUPERUSER_NAME,
   password=settings.ANON_APP_TEST_SUPERUSER_PASSWORD
  )

 def test_import_lemmings_proxies_tab_symbol_first_format(self):
  full_path = Path(self.path_to_data, 'proxy_tab_first_format.csv')
  with open(full_path) as file:
   response = self.client.post(self.api_url, {'file': file, 'file_type': 'CSV', 'delimiter': '\t',
               'import_csv_format': Proxy.ImportCsvFormatChoice.IP_PORT,
               'protocol': 'https'})
  self.assertEqual(response.status_code, 302)
  self.assertEqual(Proxy.objects.count(), 2)
  self.assertEqual(Proxy.objects.first().ip, '185.83.198.166')

 def test_import_lemmings_proxies_tab_symbol_second_format(self):
  full_path = Path(self.path_to_data, 'proxy_tab_second_format.csv')
  with open(full_path) as file:
   response = self.client.post(self.api_url, {'file': file, 'file_type': 'CSV', 'delimiter': '\t',
               'import_csv_format': Proxy.ImportCsvFormatChoice.IP_PORT_LOGIN_PASSWORD,
               'protocol': 'http'})
  self.assertEqual(response.status_code, 302)
  self.assertEqual(Proxy.objects.count(), 2)
  self.assertEqual(Proxy.objects.first().ip, '185.83.198.166')

 def test_import_lemmings_proxies_tab_symbol_third_format(self):
  full_path = Path(self.path_to_data, 'proxy_tab_third_format.csv')
  with open(full_path) as file:
   response = self.client.post(self.api_url, {'file': file, 'file_type': 'CSV', 'delimiter': '\t',
               'import_csv_format': Proxy.ImportCsvFormatChoice.LOGIN_PASSWORD_IP_PORT_LOCATION,
               'protocol': 'http'})
  self.assertEqual(response.status_code, 302)
  self.assertEqual(Proxy.objects.count(), 2)
  self.assertEqual(Proxy.objects.first().ip, '185.83.198.166')

 def test_import_lemmings_proxies_comma_first_format(self):
  full_path = Path(self.path_to_data, 'proxy_comma_first_format.csv')
  with open(full_path) as file:
   response = self.client.post(self.api_url, {'file': file, 'file_type': 'CSV', 'delimiter': ',',
               'import_csv_format': Proxy.ImportCsvFormatChoice.IP_PORT,
               'protocol': 'https'})

  self.assertEqual(response.status_code, 302)
  self.assertEqual(Proxy.objects.count(), 2)
  self.assertEqual(Proxy.objects.first().ip, '185.83.198.166')

 def test_import_lemmings_proxies_comma_second_format(self):
  full_path = Path(self.path_to_data, 'proxy_comma_second_format.csv')
  with open(full_path) as file:
   response = self.client.post(self.api_url, {'file': file, 'file_type': 'CSV', 'delimiter': ',',
               'import_csv_format': Proxy.ImportCsvFormatChoice.IP_PORT_LOGIN_PASSWORD,
               'protocol': 'https'})

  self.assertEqual(response.status_code, 302)
  self.assertEqual(Proxy.objects.count(), 2)
  self.assertEqual(Proxy.objects.first().ip, '185.83.198.166')

 def test_import_lemmings_proxies_comma_third_format(self):
  full_path = Path(self.path_to_data, 'proxy_comma_third_format.csv')
  with open(full_path) as file:
   response = self.client.post(self.api_url, {'file': file, 'file_type': 'CSV', 'delimiter': ',',
               'import_csv_format': Proxy.ImportCsvFormatChoice.LOGIN_PASSWORD_IP_PORT_LOCATION,
               'protocol': 'https'})

  self.assertEqual(response.status_code, 302)
  self.assertEqual(Proxy.objects.count(), 2)
  self.assertEqual(Proxy.objects.first().ip, '185.83.198.166')

 def test_import_lemmings_proxies_semicolon_first_format(self):
  full_path = Path(self.path_to_data, 'proxy_semicolon_first_format.csv')
  with open(full_path) as file:
   response = self.client.post(self.api_url, {'file': file, 'file_type': 'CSV', 'delimiter': ';',
               'import_csv_format': Proxy.ImportCsvFormatChoice.IP_PORT,
               'protocol': 'https'})

  self.assertEqual(response.status_code, 302)
  self.assertEqual(Proxy.objects.count(), 2)
  self.assertEqual(Proxy.objects.first().ip, '185.83.198.166')

 def test_import_lemmings_proxies_semicolon_second_format(self):
  full_path = Path(self.path_to_data, 'proxy_semicolon_second_format.csv')
  with open(full_path) as file:
   response = self.client.post(self.api_url, {'file': file, 'file_type': 'CSV', 'delimiter': ';',
               'import_csv_format': Proxy.ImportCsvFormatChoice.IP_PORT_LOGIN_PASSWORD,
               'protocol': 'https'})

  self.assertEqual(response.status_code, 302)
  self.assertEqual(Proxy.objects.count(), 2)
  self.assertEqual(Proxy.objects.first().ip, '185.83.198.166')

 def test_import_lemmings_proxies_semicolon_third_format(self):
  full_path = Path(self.path_to_data, 'proxy_semicolon_third_format.csv')
  with open(full_path) as file:
   response = self.client.post(self.api_url, {'file': file, 'file_type': 'CSV', 'delimiter': ';',
               'import_csv_format': Proxy.ImportCsvFormatChoice.LOGIN_PASSWORD_IP_PORT_LOCATION,
               'protocol': 'http'
               })

  self.assertEqual(response.status_code, 302)
  self.assertEqual(Proxy.objects.count(), 2)
  self.assertEqual(Proxy.objects.first().ip, '185.83.198.166')

 def test_import_lemmings_proxies_value_error(self):
  full_path = Path(self.path_to_data, 'proxy_semicolon_first_format.csv')
  with open(full_path) as file:
   response = self.client.post(
    self.api_url,
    {'file': file, 'file_type': 'CSV', 'delimiter': '\t',
     'import_csv_format': Proxy.ImportCsvFormatChoice.IP_PORT, 'protocol': 'https'}
   )
  self.assertEqual(response.status_code, 200)
  self.assertContains(response, 'Проверьте корректность формата файла')
  self.assertEqual(Proxy.objects.count(), 0)


class ProxyTest(TestCase):
 """Test class for proxy model manipulation cases."""

 def setUp(self):
  """Executes following before each test run."""
  self.chain = Chain.objects.create(
   title='test_chain',
   task_queue_name=''
  )
  self.proxy = Proxy.objects.create(
   ip='127.0.0.1',
   port='',
   protocol='',
   location='',
   secure_flag=Proxy.SecureFlagChoice.PAID.value,
   applying=Proxy.ApplyingChoice.UNUSED.value,
   number_of_applying=Proxy.NumberOfApplyingChoice.REUSABLE.value,
   source='',
   comment=''
  )
  self.proxy.chain = self.chain
  self.proxy.save()

 def test_proxy_state_validation(self):
  """Test for proxy state validation with not empty proxy.chain field."""
  for state in Proxy.StateChoice:
   self.proxy.state = state.value
   if self.proxy.state == 'ALIVE':
    self.assertIsNone(self.proxy.clean())
   else:
    self.assertRaises(ValidationError, self.proxy.clean)


class ChainTest(TestCase):
 """Test class for chain model manipulation cases."""
 def setUp(self):
  """Executes following before each test run."""
  proxy_data = {
   'ip': '', 'port': '', 'protocol': '', 'location': '',
   'secure_flag': Proxy.SecureFlagChoice.PAID.value,
   'applying': Proxy.ApplyingChoice.UNUSED.value,
   'number_of_applying': Proxy.NumberOfApplyingChoice.DISPOSABLE.value,
   'state': Proxy.StateChoice.ALIVE.value,
   'source': '', 'comment': '',
  }
  for index in range(2):
   proxy_data['ip'] = f'Test{index}'
   Proxy.objects.create(**proxy_data)

  self.proxy_ids = list(Proxy.objects.values_list('id', flat=True).all())
  self.file = SimpleUploadedFile('Test', b'Test')

 def test_chain_proxy_validation(self):
  """Test for Chain model and form clean methods.

  Notes
  -----
  The test_options attribute contains tuples with four parameters for tests:
  test_options[..][0]: bool : the Chain.has_proxies_chain field
  test_options[..][1]: int : the Chain.proxies_in_chain field
  test_options[..][2]: int : the count of proxies to be passed to the Chain form
  test_options[..][3]: bool : the form.is_valid assertion
  """
  test_options = (
   (True, 2, 1, False), (True, 1, 2, True), (True, 2, 2, True), # tests for ChainAdminForm.clean
   (True, 0, 0, False), (True, 0, 1, False), (True, 0, 2, False), # tests for Chain.clean
   (False, 0, 1, True), (False, 1, 1, False), (False, 2, 2, False), # tests for Chain.clean
  )
  form_data = {
   'proxy': [], 'proxies_in_chain': 0, 'has_proxies_chain': False,
   'title': 'Test', 'task_queue_name': 'Test', 'status': Chain.StatusChoice.READY.value,
   'openssh_container_external_port': 1024, 'openssh_container_internal_port': 1024,
   'proxy_limit': 1, 'concurrency': 1,
  }
  form_files = {
   'openssh_container_id_rsa': self.file,
   'openssh_container_id_rsa_pub': self.file,
  }

  for has_proxies_chain, proxies_in_chain, proxies_count, is_valid in test_options:
   form_data['has_proxies_chain'] = has_proxies_chain
   form_data['proxy'] = self.proxy_ids[:proxies_count]
   form_data['proxies_in_chain'] = proxies_in_chain
   form = ChainAdminForm(data=form_data, files=form_files)
   if is_valid:
    self.assertTrue(form.is_valid())
   else:
    self.assertFalse(form.is_valid())