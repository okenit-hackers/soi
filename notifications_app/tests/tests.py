import datetime

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITransactionTestCase

from anon_app import conf
from anon_app.utils import create_test_users
from ..models import Notification


class NotificationViewTest(APITransactionTestCase):
 url = reverse('notification-list')
 is_logined = False
 need_admin_user = True

 def setUp(self):
  create_test_users()
  Notification.send_to_all('Цепочка построилась', log_level=Notification.LogLevelChoice.COLOR_INFO.value)

  if not self.is_logined:
   self.client.login(
    username=conf.settings.ANON_APP_TEST_USER_NAME if not self.need_admin_user
    else conf.settings.ANON_APP_TEST_SUPERUSER_NAME,
    password=conf.settings.ANON_APP_TEST_USER_PASSWORD if not self.need_admin_user
    else conf.settings.ANON_APP_TEST_SUPERUSER_PASSWORD,
   )
   self.is_logined = True
  self.request_factory = APIRequestFactory()

 def test_list(self):
  response = self.client.get(self.url)
  self.assertEqual(1, len(response.data['results'])) # для конкретного пользователя только одно уведомление
  self.assertEqual(Notification.objects.count(), 2)
  self.assertEqual(response.data['results'][0]['content'], 'Цепочка построилась')
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')

 def test_create(self):
  Notification.send_to_current_user(
   User.objects.last(), 'some_content', log_level=Notification.LogLevelChoice.COLOR_INFO.value
  )
  self.assertEqual(Notification.objects.count(), 3)

 def test_get(self):
  notification = Notification.objects.first()
  response = self.client.get(reverse('notification-detail', kwargs={'pk': notification.pk}))
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual(response.data['content'], 'Цепочка построилась')

 def test_put_valid(self):
  notification = Notification.objects.first()
  data = {'content': 'Chain rebuild', 'send_date': datetime.datetime.now()}
  response = self.client.put(reverse('notification-detail', kwargs={'pk': notification.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertIsNotNone(response.data['send_date'])

 def test_patch_valid(self):
  notification = Notification.objects.first()
  data = {'seen_date': datetime.datetime.now()}
  response = self.client.patch(reverse('notification-detail', kwargs={'pk': notification.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertIsNotNone(response.data['seen_date'])