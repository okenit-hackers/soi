import csv
import json
import random
from io import TextIOWrapper

from django.contrib.auth.models import User
from django.core import serializers

from anon_app.conf import settings
from anon_app.exceptions import ServiceNotAvailableError
from anon_app.models import Chain, Proxy


def create_test_users():
 if User.objects.filter(username=settings.ANON_APP_TEST_USER_NAME).exists():
  User.objects.get(username=settings.ANON_APP_TEST_USER_NAME).delete()
 if User.objects.filter(username=settings.ANON_APP_TEST_SUPERUSER_NAME).exists():
  User.objects.get(username=settings.ANON_APP_TEST_SUPERUSER_NAME).delete()

 User.objects.create_superuser(
  username=settings.ANON_APP_TEST_SUPERUSER_NAME,
  password=settings.ANON_APP_TEST_SUPERUSER_PASSWORD
 )
 User.objects.create_user(
  username=settings.ANON_APP_TEST_USER_NAME,
  password=settings.ANON_APP_TEST_USER_PASSWORD
 )


def handle_proxies_from_csv(
  in_file, delimiter, csv_format, protocol,
  secure_flag, number_of_applying,
  applying, source, comment, anon_chain
):
 """
 Создает записи в Proxy из считанного содержимого csv файла,
 также вызывает celery цепочку задач для асинхронной проверки прокси серверов
 """
 f = TextIOWrapper(in_file, encoding='UTF-8')
 blacklisted_proxies_query = Proxy.objects.filter(applying=Proxy.ApplyingChoice.BLACKLIST)
 proxies = []

 with f as csvfile:
  reader = csv.reader(csvfile, delimiter=delimiter)

  if csv_format == Proxy.ImportCsvFormatChoice.IP_PORT:
   for ip, port in reader:

    if not blacklisted_proxies_query.filter(ip=ip).exists():
     proxies.append(
      Proxy(protocol=protocol.lower(), ip=ip, port=port, secure_flag=secure_flag,
        number_of_applying=number_of_applying, applying=applying, source=source, comment=comment)
     )

  elif csv_format == Proxy.ImportCsvFormatChoice.IP_PORT_LOGIN_PASSWORD:
   for ip, port, username, password in reader:

    if not blacklisted_proxies_query.filter(ip=ip).exists():
     proxies.append(
      Proxy(protocol=protocol.lower(), ip=ip, port=port, username=username, password=password,
        secure_flag=secure_flag, number_of_applying=number_of_applying, applying=applying,
        source=source, comment=comment)
     )

  elif csv_format == Proxy.ImportCsvFormatChoice.LOGIN_PASSWORD_IP_PORT_LOCATION:
   for username, password, ip, port, location in reader:

    if not blacklisted_proxies_query.filter(ip=ip).exists():
     proxies.append(
      Proxy(protocol=protocol.lower(), username=username, password=password, ip=ip, port=port,
        location=location, secure_flag=secure_flag, number_of_applying=number_of_applying,
        applying=applying, source=source, comment=comment)
     )
  # делает запрос на создание сразу всего списка прокси в БД
  Proxy.objects.bulk_create(proxies)

  # создает цепочку задач celery для асинхронной проверки прокси и для изменения статуса прокси в БД
  if anon_chain:
   proxies = json.loads(serializers.serialize('json', proxies))
   tasks_chain = anon_chain.create_tasks_chain_for_proxies(proxies, check_proxies_location=True)
   tasks_chain.apply_async()


def get_proxy(chain_pk: int):
 """Возвращает один живой Proxy: dict по chain_pk"""
 anon_chain = Chain.objects.get(pk=chain_pk)
 proxies_count = anon_chain.get_alive_proxies_query_with_conditions().count()
 if proxies_count != 0:
  proxy = json.loads(
   serializers.serialize('json', [anon_chain.get_alive_proxies_query_with_conditions().first()]))
  return proxy[0]
 return None


class ProxyChanger:
 def __init__(self, proxies: list[dict], current_proxy: dict, service: str):
  self.service = service
  self.current_proxy = current_proxy
  self.proxies = proxies
  self.used_proxies = []

 def _update_proxy_data(self):
  self.proxies.remove(self.current_proxy)
  self.used_proxies.append(self.current_proxy)

 @staticmethod
 def save_proxy_data(proxies: list[dict], service: str):
  # save to DB
  if proxies is None:
   return
  for p in proxies:
   proxy = Proxy.objects.get(pk=p['pk'])
   try:
    service_dict = proxy.services[service.lower()]
   except KeyError:
    proxy.services[service.lower()] = {
     'attempts': 1,
     'banned': False
    }
   else:
    service_dict['attempts'] += 1
    if service_dict['attempts'] >= 5:
     service_dict['banned'] = True
   finally:
    proxy.save()

 def change_proxy(self):
  if self.current_proxy is None:
   raise ServiceNotAvailableError('there is no any proxies available for this task')
  self._update_proxy_data()
  self._choose_proxy()
  return self.proxies, self.current_proxy, self.used_proxies

 def _choose_proxy(self):
  if not self.proxies:
   self.current_proxy = None
   return
  self.current_proxy = random.choice(self.proxies)