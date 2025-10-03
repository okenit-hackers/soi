from django.contrib.auth.models import User
from rest_framework import status
from rest_framework.test import APITestCase


class BotAccountViewSetTestCase(APITestCase):
 fixtures = ("fixture_chain.json",
    "fixture_botaccount.json",)

 def setUp(self):
  self.superuser = User.objects.get(pk=1)
  self.bot_id_1 = 276

 def test_list_bot_accounts(self):
  self.client.force_authenticate(user=self.superuser)
  response = self.client.get('/botaccount/')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual(len(response.data['results']), 3)
  self.assertEqual(response.data['results'][2]['id'], self.bot_id_1)

 def test_get_bot_account(self):
  self.client.force_authenticate(user=self.superuser)
  response = self.client.get(f'/botaccount/{self.bot_id_1}/')
  fields = ['id', 'service', 'username', 'phone_number', 'chain_id', 'account_state', 'created', 'changed', 'service_account']
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual(response.data['id'], self.bot_id_1)
  for field in response.data.keys():
   with self.subTest():
    self.assertIn(field, fields)

 def test_list_bot_accounts_unauthenticated(self):
  response = self.client.get('/botaccount/')
  self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

 def test_delete_bot_account(self):
  self.client.force_authenticate(user=self.superuser)
  response = self.client.delete(f'/botaccount/{self.bot_id_1}/')
  self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
  response = self.client.get(f'/botaccount/{self.bot_id_1}/')
  self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)