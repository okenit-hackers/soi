import datetime
import json
import time
from base64 import b64decode
from pathlib import Path

from rest_framework import status

from anon_app import conf
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django_celery_beat.models import IntervalSchedule, CrontabSchedule, SolarSchedule, SOLAR_SCHEDULES, \
 ClockedSchedule, PeriodicTask, PeriodicTasks
from redis import Redis

from rest_framework.test import APIRequestFactory, APITransactionTestCase
from anon_app.conf import settings
from anon_app.models import Chain
from anon_app.utils import create_test_users
from lemmings_app.models import LemmingsTask, BotAccount, BehaviorBots
from lemmings_app.tasks import run_lemmings_task
from lemmings_app.tests.datasource import get_new_lmgs_task_data
from lemmings.services_enum import Service as LemmingsService
from soi_app.settings import REDIS_HOST, REDIS_PORT, REDIS_BROCKER_DATABASE_NUMBER, DATA_PREFIX


class CeleryTaskRoutingTest(TestCase):
 _redis: Redis
 _created_queue_name: str

 @classmethod
 def setUpClass(cls):
  super(CeleryTaskRoutingTest, cls).setUpClass()
  create_test_users()
  cls._redis = Redis(
   host=REDIS_HOST,
   port=REDIS_PORT,
   db=REDIS_BROCKER_DATABASE_NUMBER
  )

 def setUp(self) -> None:
  self.client.login(
   username=settings.ANON_APP_TEST_SUPERUSER_NAME,
   password=settings.ANON_APP_TEST_SUPERUSER_PASSWORD
  )

 def test_lmgs_task_routing(self):
  self._created_queue_name = f'test_queue_{time.time()}'
  lmgs_task_data = get_new_lmgs_task_data(task_queue_name=self._created_queue_name)
  lmgs_task_instance = LemmingsTask.objects.create(**lmgs_task_data)
  run_lemmings_task.delay(
   task_identifier=f'lmgs:{lmgs_task_instance.id}',
   action=lmgs_task_instance.action,
   lmgs_kwargs=lmgs_task_instance.kwargs,
   queue_name=lmgs_task_instance.chain.task_queue_name
  )

  _, kwargs, *_ = json.loads(b64decode(json.loads(self._redis.lpop(self._created_queue_name))['body']))
  queue_name = kwargs['queue_name']
  self.assertEqual(queue_name, self._created_queue_name)

 def tearDown(self) -> None:
  if getattr(self, '_created_queue_name', None) is None:
   return
  self._redis.delete(*self._created_queue_name)
  self._created_queue_name = None

 @classmethod
 def tearDownClass(cls):
  super(CeleryTaskRoutingTest, cls).tearDownClass()

  if getattr(cls, '_redis', None) is None:
   return
  cls._redis.close()


class TestImportAccounts(TestCase):
 api_url = reverse('import-bots')
 path_to_data = Path(DATA_PREFIX, 'lemmings_app', 'tests', 'import_data')

 @classmethod
 def setUpClass(cls):
  super(TestImportAccounts, cls).setUpClass()
  create_test_users()
  chain = Chain.objects.create()

 def setUp(self) -> None:
  self.client.login(
   username=settings.ANON_APP_TEST_SUPERUSER_NAME,
   password=settings.ANON_APP_TEST_SUPERUSER_PASSWORD
  )

 def test_import_lemmings_bots_tab_symbol(self):
  full_path = Path(self.path_to_data, 'accounts_tab.csv')
  chain = Chain.objects.first()
  with open(full_path) as file:
   response = self.client.post(self.api_url,
          {'file': file, 'file_type': 'CSV', 'delimiter': '\t', 'chain': chain.pk})

  self.assertEqual(response.status_code, 302)
  self.assertEqual(len(BotAccount.objects.all()), 9)
  self.assertEqual(BotAccount.objects.all()[0].email, 'e.mail@mail.ru')

 def test_import_lemmings_bots_comma(self):
  full_path = Path(self.path_to_data, 'accounts_comma.csv')
  chain = Chain.objects.first()
  with open(full_path) as file:
   response = self.client.post(
    self.api_url,
    {'file': file, 'file_type': 'CSV', 'delimiter': ',', 'chain': chain.pk}
   )

  self.assertEqual(response.status_code, 302)
  self.assertEqual(len(BotAccount.objects.all()), 9)
  self.assertEqual(BotAccount.objects.all()[0].email, 'e.mail@mail.ru')

 def test_import_lemmings_bots_semicolon(self):
  full_path = Path(self.path_to_data, 'accounts_semicolon.csv')
  chain = Chain.objects.first()
  with open(full_path) as file:
   response = self.client.post(
    self.api_url,
    {'file': file, 'file_type': 'CSV', 'delimiter': ';', 'chain': chain.pk},
   )

  self.assertEqual(response.status_code, 302)
  self.assertEqual(len(BotAccount.objects.all()), 9)
  self.assertEqual(BotAccount.objects.all()[0].email, 'e.mail@mail.ru')

 def test_import_lemmings_bots_value_error(self):
  full_path = Path(self.path_to_data, 'accounts_semicolon.csv')
  chain = Chain.objects.first()
  with open(full_path) as file:
   response = self.client.post(
    self.api_url,
    {'file': file, 'file_type': 'CSV', 'delimiter': '\t', 'chain': chain.pk},
   )
  self.assertEqual(response.status_code, 200)
  self.assertContains(response, 'Проверьте корректность формата файла')
  self.assertEqual(len(BotAccount.objects.all()), 0)

 def test_import_lemmings_bots_double_data(self):
  full_path = Path(self.path_to_data, 'accounts_tab.csv')
  second_full_path = Path(self.path_to_data, 'accounts_tab_2.csv')
  chain = Chain.objects.first()
  with open(full_path) as file:
   response = self.client.post(
    self.api_url,
    {'file': file, 'file_type': 'CSV', 'delimiter': '\t', 'chain': chain.pk}
   )
  self.assertEqual(response.status_code, 302)
  self.assertEqual(len(BotAccount.objects.all()), 9)
  self.assertEqual(BotAccount.objects.all()[0].email, 'e.mail@mail.ru')

  with open(second_full_path) as file:
   response = self.client.post(
    self.api_url,
    {'file': file, 'file_type': 'CSV', 'delimiter': '\t', 'chain': chain.pk}
   )

  self.assertEqual(response.status_code, 302)
  self.assertEqual(len(BotAccount.objects.all()), 9)
  self.assertEqual(BotAccount.objects.all()[0].email, 'e.mail@mail.ru')


class BehaviorBotsViewTest(APITransactionTestCase):
 url = reverse('behaviorbots-list')
 is_logined = False

 def setUp(self):
  interval = IntervalSchedule.objects.create(every=10, period=IntervalSchedule.PERIOD_CHOICES[0][0])
  CrontabSchedule.objects.create(minute=10)
  SolarSchedule.objects.create(event=SOLAR_SCHEDULES[0][0], latitude=10, longitude=10)
  ClockedSchedule.objects.create(clocked_time=datetime.datetime.now())
  BehaviorBots.objects.create(name='Домохозяйка', interval=interval)
  create_test_users()

  if not self.is_logined:
   self.client.login(
    username=conf.settings.ANON_APP_TEST_USER_NAME,
    password=conf.settings.ANON_APP_TEST_USER_PASSWORD,
   )
   self.is_logined = True
  self.request_factory = APIRequestFactory()

 def test_list(self):
  response = self.client.get(self.url)
  behavior_bots = BehaviorBots.objects.last()
  periodic_task = PeriodicTask.objects.last()
  self.assertEqual(BehaviorBots.objects.count(), len(response.data['results']))
  self.assertEqual(behavior_bots.interval, periodic_task.interval)
  self.assertEqual(behavior_bots.periodic_task, periodic_task)
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')

 def test_valid_create(self):
  crontab_schedule = CrontabSchedule.objects.last()
  data = {
   'crontab': reverse('crontabschedule-detail', kwargs={'pk': crontab_schedule.pk}),
   'name': 'Домохозяин'
  }
  response = self.client.post(self.url, data, format='json')
  behavior_bots = BehaviorBots.objects.first()
  periodic_task = PeriodicTask.objects.last()
  self.assertEqual(response.status_code, status.HTTP_201_CREATED)
  self.assertEqual(BehaviorBots.objects.count(), 2)
  self.assertEqual(behavior_bots.periodic_task, periodic_task)
  self.assertEqual(behavior_bots.crontab, periodic_task.crontab)
  self.assertEqual(crontab_schedule, behavior_bots.crontab)

 def test_invalid_name_create(self):
  crontab_schedule = CrontabSchedule.objects.last()
  data = {
   'crontab': reverse('crontabschedule-detail', kwargs={'pk': crontab_schedule.pk}),
   'name': 'Домохозяйка'
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(BehaviorBots.objects.count(), 1)

 def test_invalid_interval_none_create(self):
  data = {
   'name': 'Домохозяин'
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(BehaviorBots.objects.count(), 1)

 def test_valid_solar_create(self):
  solar_schedule = SolarSchedule.objects.last()
  data = {
   'solar': reverse('solarschedule-detail', kwargs={'pk': solar_schedule.pk}),
   'name': 'Домохозяин'
  }
  response = self.client.post(self.url, data, format='json')
  behavior_bots = BehaviorBots.objects.first()
  periodic_task = PeriodicTask.objects.last()
  self.assertEqual(response.status_code, status.HTTP_201_CREATED)
  self.assertEqual(BehaviorBots.objects.count(), 2)
  self.assertEqual(behavior_bots.periodic_task, periodic_task)
  self.assertEqual(behavior_bots.solar, periodic_task.solar)
  self.assertEqual(solar_schedule, behavior_bots.solar)

 def test_invalid_clocked_schedule_create(self):
  clocked_schedule = ClockedSchedule.objects.last()
  data = {
   'clocked': reverse('clockedschedule-detail', kwargs={'pk': clocked_schedule.pk}),
   'name': 'Домохозяин'
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(BehaviorBots.objects.count(), 1)

 def test_valid_clocked_schedule_create(self):
  clocked_schedule = ClockedSchedule.objects.last()
  data = {
   'clocked': reverse('clockedschedule-detail', kwargs={'pk': clocked_schedule.pk}),
   'name': 'Домохозяин', 'one_off': True
  }
  response = self.client.post(self.url, data, format='json')
  behavior_bots = BehaviorBots.objects.first()
  periodic_task = PeriodicTask.objects.last()
  self.assertEqual(response.status_code, status.HTTP_201_CREATED)
  self.assertEqual(BehaviorBots.objects.count(), 2)
  self.assertEqual(behavior_bots.periodic_task, periodic_task)
  self.assertEqual(behavior_bots.clocked, periodic_task.clocked)
  self.assertEqual(clocked_schedule, behavior_bots.clocked)


class BotCreationTests(TestCase):
 """Test class for bot creation cases."""

 def setUp(self):
  """Executes following before each test run."""
  test_chain = Chain.objects.create(
   title='test',
   task_queue_name='test',
   status='CREATING',
   openssh_container_external_port=1025,
   openssh_container_internal_port=1025,
   proxy_limit=11,
   concurrency=1,
  )

 def test_name_validation_name_rus_surname_eng(self):
  """Test for name validation when name in russian and surname in english."""
  test_bot = BotAccount.objects.create(
    chain=Chain.objects.get(title='test'),
    service=LemmingsService.VK.name,
    first_name='Ипполит',
    last_name='Ippolit',
   )
  self.assertRaises(ValidationError, test_bot.clean)

 def test_name_validation_name_eng_surname_rus(self):
  """Test for name validation when name in english and surname in russian."""
  test_bot = BotAccount.objects.create(
    chain=Chain.objects.get(title='test'),
    service=LemmingsService.VK.name,
    first_name='Ippolit',
    last_name='Ипполит',
   )
  self.assertRaises(ValidationError, test_bot.clean)

 def test_name_validation_name_rus_surname_empty(self):
  """Test for name validation when name in russian and surname is empty."""
  test_bot = BotAccount.objects.create(
    chain=Chain.objects.get(title='test'),
    service=LemmingsService.VK.name,
    first_name='Ипполит',
   )
  self.assertRaises(ValidationError, test_bot.clean)

 def test_name_validation_name_empty_surname_rus(self):
  """Test for name validation when name is empty and surname in russian."""
  test_bot = BotAccount.objects.create(
    chain=Chain.objects.get(title='test'),
    service=LemmingsService.VK.name,
    last_name='Ипполит',
   )
  self.assertRaises(ValidationError, test_bot.clean)