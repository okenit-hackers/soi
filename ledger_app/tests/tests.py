from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITransactionTestCase

from anon_app.conf import settings
from anon_app import conf
from anon_app.utils import create_test_users
from ledger_app.models import Currency, PaidService, ServiceAccount, Ledger, PhoneRent, PhoneRentAccount


class CurrencyViewTest(APITransactionTestCase):
 url = reverse('currency-list')
 is_logined = False

 def setUp(self):
  Currency.objects.create(name='RUB')
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
  self.assertEqual(Currency.objects.count(), len(response.data['results']))
  self.assertEqual(Currency.objects.get().name, 'RUB')
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')

 def test_create(self):
  data = {'name': 'bitcoin'}
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_201_CREATED)
  self.assertEqual(Currency.objects.count(), 2)
  self.assertEqual(Currency.objects.get(name='bitcoin').name, 'bitcoin')

 def test_get(self):
  currency = Currency.objects.first()
  response = self.client.get(reverse('currency-detail', kwargs={'pk': currency.pk}))
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual(response.data, {'name': 'RUB'})

 def test_put_valid(self):
  currency = Currency.objects.first()
  data = {'name': 'USD'}
  response = self.client.put(reverse('currency-detail', kwargs={'pk': currency.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual(response.data, {'name': 'USD'})

 def test_patch_valid(self):
  currency = Currency.objects.first()
  data = {'name': 'USD'}
  response = self.client.patch(reverse('currency-detail', kwargs={'pk': currency.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual(response.data, data)


class PaidServiceViewTest(APITransactionTestCase):
 url = reverse('paidservice-list')
 is_logined = False

 def setUp(self):
  PaidService.objects.create(name='qiwi', url='https://qiwi.com', note='some note')
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
  self.assertEqual(PaidService.objects.count(), len(response.data['results']))
  self.assertEqual(PaidService.objects.get().name, 'qiwi')
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')

 def test_valid_create(self):
  data = {'name': 'bitcoin', 'url': 'https://www.bitcoin.com/', 'note': 'service about bitcoin'}
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_201_CREATED)
  self.assertEqual(PaidService.objects.count(), 2)
  self.assertEqual(PaidService.objects.get(name='bitcoin').name, 'bitcoin')

 def test_invalid_create(self):
  data = {'name': 'invalid_service', 'url': 'fsjfhsfjsadfsdsahdsdjfs'}
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(PaidService.objects.count(), 1)

 def test_put_valid(self):
  paid_service = PaidService.objects.first()
  data = {'name': 'drf', 'url': 'https://www.django-rest-framework.org/', 'note': ''}
  response = self.client.put(reverse('paidservice-detail', kwargs={'pk': paid_service.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual(response.data, data)

 def test_put_invalid(self):
  paid_service = PaidService.objects.first()
  data = {'name': 'sdasdas', 'url': '53fgdg4333333', 'note': 'gdfdsddf'}
  response = self.client.put(reverse('paidservice-detail', kwargs={'pk': paid_service.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_put_invalid_note_none(self):
  paid_service = PaidService.objects.first()
  data = {'name': 'github', 'url': 'https://github.com/', 'note': None}
  response = self.client.put(reverse('paidservice-detail', kwargs={'pk': paid_service.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_put_invalid_none(self):
  paid_service = PaidService.objects.first()
  data = {'name': None, 'url': None, 'note': None}
  response = self.client.put(reverse('paidservice-detail', kwargs={'pk': paid_service.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertIsNotNone(paid_service.name)

 def test_patch_valid(self):
  paid_service = PaidService.objects.first()
  data = {'name': 'I\'m changed'}
  response = self.client.patch(reverse('paidservice-detail', kwargs={'pk': paid_service.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual(response.data['name'], data['name'])

 def test_patch_invalid(self):
  paid_service = PaidService.objects.first()
  data = {'url': 'invalid_url'}
  response = self.client.patch(reverse('paidservice-detail', kwargs={'pk': paid_service.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_patch_invalid_none(self):
  paid_service = PaidService.objects.first()
  data = {'name': None}
  response = self.client.patch(reverse('paidservice-detail', kwargs={'pk': paid_service.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertIsNotNone(paid_service.name)


class ServiceAccountViewTest(APITransactionTestCase):
 url = reverse('serviceaccount-list')
 is_logined = False

 def setUp(self):
  paid_service = PaidService.objects.create(name='qiwi', url='https://qiwi.com', note='some note')
  ServiceAccount.objects.create(username='tifox', password='qwerty', service=paid_service)
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
  self.assertEqual(ServiceAccount.objects.count(), len(response.data['results']))
  self.assertEqual(ServiceAccount.objects.get().username, 'tifox')
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')

 def test_valid_create(self):
  paid_service = PaidService.objects.last()
  data = {'username': 'bitcoin_magnate', 'password': 'sdsdasdsad',
    'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk})}
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_201_CREATED)
  self.assertEqual(ServiceAccount.objects.count(), 2)
  self.assertEqual(ServiceAccount.objects.get(username='bitcoin_magnate').username, 'bitcoin_magnate')

 def test_invalid_username_create(self):
  paid_service = PaidService.objects.last()
  data = {
   'username': None, 'password': 'sdsdasdsad',
   'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk})
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_invalid_password_create(self):
  paid_service = PaidService.objects.last()
  data = {
   'username': 'karlen-molodec', 'password': None,
   'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk})
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_invalid_paid_service_create(self):
  data = {
   'username': 'karlen-molodec', 'password': 'sfsafdsad',
   'service': reverse('paidservice-detail', kwargs={'pk': 423213})
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_put_valid(self):
  service_account = ServiceAccount.objects.first()
  paid_service = PaidService.objects.create(
   name='drf', url='https://www.django-rest-framework.org/api-guide/testing/', note='it can help'
  )
  data = {
   'username': 'some_user', 'password': 'sdasda',
   'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk})
  }
  response = self.client.put(reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}), data,
          format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual(response.data['username'], data['username'])
  self.assertEqual(response.data['password'], data['password'])

 def test_put_username_invalid(self):
  service_account = ServiceAccount.objects.first()
  paid_service = PaidService.objects.create(
   name='drf', url='https://www.django-rest-framework.org/api-guide/testing/', note='it can help'
  )
  data = {
   'username': None, 'password': 'sdasda',
   'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk})
  }
  response = self.client.put(reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}), data,
          format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_put_password_invalid(self):
  service_account = ServiceAccount.objects.first()
  paid_service = PaidService.objects.create(
   name='drf', url='https://www.django-rest-framework.org/api-guide/testing/', note='it can help'
  )
  data = {
   'username': 'sdfasdsad', 'password': None,
   'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk})
  }
  response = self.client.put(reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}), data,
          format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_put_paid_service_invalid(self):
  service_account = ServiceAccount.objects.first()
  data = {
   'username': 'sdfasdsad',
   'password': None,
   'service': reverse('paidservice-detail', kwargs={'pk': 32312321})
  }
  response = self.client.put(reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}), data,
          format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_patch_valid(self):
  service_account = ServiceAccount.objects.first()
  data = {'username': 'karlen-is-the-best'}
  response = self.client.patch(reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}), data,
          format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual(response.data['username'], data['username'])

 def test_patch_invalid(self):
  service_account = ServiceAccount.objects.first()
  data = {'username': None}
  response = self.client.patch(reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}), data,
          format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_patch_invalid_max(self):
  service_account = ServiceAccount.objects.first()
  data = {'username': 'python' * 100}
  response = self.client.patch(reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}), data,
          format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class LedgerViewTest(APITransactionTestCase):
 url = reverse('ledger-list')
 is_logined = False

 def setUp(self):
  paid_service = PaidService.objects.create(name='qiwi', url='https://qiwi.com', note='some note')
  service_account = ServiceAccount.objects.create(username='tifox', password='qwerty', service=paid_service)
  currency = Currency.objects.create(name='RUB')
  Ledger.objects.create(service=paid_service, account=service_account, currency=currency, balance=3231.21)
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
  self.assertEqual(Ledger.objects.count(), len(response.data['results']))
  self.assertEqual(Ledger.objects.get().account.username, 'tifox')
  self.assertEqual(Ledger.objects.get().service.name, 'qiwi')
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')

 def test_valid_create(self):
  service_account = ServiceAccount.objects.last()
  currency = Currency.objects.last()
  paid_service = PaidService.objects.create(
   name='drf', url='https://www.django-rest-framework.org/api-guide/testing/', note='some note'
  )
  data = {
   'balance': 3231.42, 'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk}),
   'currency': reverse('currency-detail', kwargs={'pk': currency.pk}),
   'account': reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}),
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_201_CREATED)
  self.assertEqual(Ledger.objects.count(), 2)
  self.assertEqual(Ledger.objects.get(service__name='drf').service.name, 'drf')

 def test_invalid_balance_str_create(self):
  service_account = ServiceAccount.objects.last()
  currency = Currency.objects.last()
  paid_service = PaidService.objects.last()
  data = {
   'balance': 'gdsfdsfs', 'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk}),
   'currency': reverse('currency-detail', kwargs={'pk': currency.pk}),
   'account': reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}),
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_invalid_balance_none_create(self):
  service_account = ServiceAccount.objects.last()
  currency = Currency.objects.last()
  paid_service = PaidService.objects.last()
  data = {
   'balance': None, 'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk}),
   'currency': reverse('currency-detail', kwargs={'pk': currency.pk}),
   'account': reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}),
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_invalid_currency_create(self):
  service_account = ServiceAccount.objects.last()
  paid_service = PaidService.objects.last()
  data = {
   'balance': 4123.23, 'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk}),
   'currency': reverse('currency-detail', kwargs={'pk': 321123}),
   'account': reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}),
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_invalid_paid_service_create(self):
  service_account = ServiceAccount.objects.last()
  currency = Currency.objects.last()
  data = {
   'balance': 4123.23, 'service': reverse('paidservice-detail', kwargs={'pk': 4324234}),
   'currency': reverse('currency-detail', kwargs={'pk': currency.pk}),
   'account': reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}),
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_invalid_service_account_create(self):
  paid_service = PaidService.objects.last()
  currency = Currency.objects.last()
  data = {
   'balance': 4123.23, 'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk}),
   'currency': reverse('currency-detail', kwargs={'pk': currency.pk}),
   'account': reverse('serviceaccount-detail', kwargs={'pk': 42342}),
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_invalid_currency_none_create(self):
  service_account = ServiceAccount.objects.last()
  paid_service = PaidService.objects.last()
  data = {
   'balance': 4123.23, 'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk}),
   'currency': None,
   'account': reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}),
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_invalid_currency_str_create(self):
  service_account = ServiceAccount.objects.last()
  paid_service = PaidService.objects.last()
  data = {
   'balance': 4123.23, 'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk}),
   'currency': 'fdfdsfdsf',
   'account': reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}),
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_invalid_paid_service_none_create(self):
  service_account = ServiceAccount.objects.last()
  currency = Currency.objects.last()
  data = {
   'balance': 4123.23, 'service': None,
   'currency': reverse('currency-detail', kwargs={'pk': currency.pk}),
   'account': reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}),
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_invalid_paid_service_str_create(self):
  service_account = ServiceAccount.objects.last()
  currency = Currency.objects.last()
  data = {
   'balance': 4123.23, 'service': 'fsdfsdfs',
   'currency': reverse('currency-detail', kwargs={'pk': currency.pk}),
   'account': reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}),
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_invalid_service_account_none_create(self):
  paid_service = PaidService.objects.last()
  currency = Currency.objects.last()
  data = {
   'balance': 4123.23, 'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk}),
   'currency': reverse('currency-detail', kwargs={'pk': currency.pk}),
   'account': None,
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_invalid_service_account_str_create(self):
  paid_service = PaidService.objects.last()
  currency = Currency.objects.last()
  data = {
   'balance': 4123.23, 'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk}),
   'currency': reverse('currency-detail', kwargs={'pk': currency.pk}),
   'account': 'sdsdsadas',
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_invalid_none_account_create(self):
  data = {
   'balance': None,
   'service': None,
   'currency': None,
   'account': None,
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(ServiceAccount.objects.count(), 1)

 def test_put_valid(self):
  ledger = Ledger.objects.last()
  paid_service = PaidService.objects.create(name='github', url='https://github.com/', note='')
  service_account = ServiceAccount.objects.create(username='user', password='qweqweqwqweqweqweqwe',
              service=paid_service)
  currency = Currency.objects.create(name='bitcoin')
  data = {
   'balance': 0.1242, 'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk}),
   'currency': reverse('currency-detail', kwargs={'pk': currency.pk}),
   'account': reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}),
  }
  response = self.client.put(reverse('ledger-detail', kwargs={'pk': ledger.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertTrue(str(data['balance']) in response.data['balance'])

 def test_put_balance_invalid(self):
  ledger = Ledger.objects.last()
  paid_service = PaidService.objects.create(name='github', url='https://github.com/', note='')
  service_account = ServiceAccount.objects.create(username='user', password='qweqweqwqweqweqweqwe',
              service=paid_service)
  currency = Currency.objects.create(name='bitcoin')
  data = {
   'balance': None, 'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk}),
   'currency': reverse('currency-detail', kwargs={'pk': currency.pk}),
   'account': reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}),
  }
  response = self.client.put(reverse('ledger-detail', kwargs={'pk': ledger.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_put_currency_invalid(self):
  ledger = Ledger.objects.last()
  paid_service = PaidService.objects.create(name='github', url='https://github.com/', note='')
  service_account = ServiceAccount.objects.create(username='user', password='qweqweqwqweqweqweqwe',
              service=paid_service)
  data = {
   'balance': 34242.21, 'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk}),
   'currency': reverse('currency-detail', kwargs={'pk': 423321}),
   'account': reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}),
  }
  response = self.client.put(reverse('ledger-detail', kwargs={'pk': ledger.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_put_paid_service_invalid(self):
  ledger = Ledger.objects.last()
  currency = Currency.objects.create(name='bitcoin')
  paid_service = PaidService.objects.create(name='github', url='https://github.com/', note='')
  service_account = ServiceAccount.objects.create(username='user', password='qweqweqwqweqweqweqwe',
              service=paid_service)
  data = {
   'balance': 34242.21, 'service': reverse('paidservice-detail', kwargs={'pk': 4213}),
   'currency': reverse('currency-detail', kwargs={'pk': currency.pk}),
   'account': reverse('serviceaccount-detail', kwargs={'pk': service_account.pk}),
  }
  response = self.client.put(reverse('ledger-detail', kwargs={'pk': ledger.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_put_paid_service_account_invalid(self):
  ledger = Ledger.objects.last()
  currency = Currency.objects.create(name='bitcoin')
  paid_service = PaidService.objects.create(name='github', url='https://github.com/', note='')
  data = {
   'balance': 34242.21, 'service': reverse('paidservice-detail', kwargs={'pk': paid_service.pk}),
   'currency': reverse('currency-detail', kwargs={'pk': currency.pk}),
   'account': reverse('serviceaccount-detail', kwargs={'pk': 32131}),
  }
  response = self.client.put(reverse('ledger-detail', kwargs={'pk': ledger.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_put_none_invalid(self):
  ledger = Ledger.objects.last()
  data = {
   'balance': None,
   'service': None,
   'currency': None,
   'account': None,
  }
  response = self.client.put(reverse('ledger-detail', kwargs={'pk': ledger.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_patch_valid_currency_str(self):
  ledger = Ledger.objects.last()
  currency = Currency.objects.create(name='USD')
  data = {
   'currency': reverse('currency-detail', kwargs={'pk': currency.pk}),
  }
  response = self.client.patch(reverse('ledger-detail', kwargs={'pk': ledger.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual('USD', Ledger.objects.last().currency.name)

 def test_patch_valid(self):
  ledger = Ledger.objects.last()
  data = {
   'balance': 2313.231,
  }
  response = self.client.patch(reverse('ledger-detail', kwargs={'pk': ledger.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertTrue(str(data['balance']) in response.data['balance'])

 def test_patch_invalid_balance_none(self):
  ledger = Ledger.objects.last()
  data = {
   'balance': None,
  }
  response = self.client.patch(reverse('ledger-detail', kwargs={'pk': ledger.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_patch_invalid_balance_str(self):
  ledger = Ledger.objects.last()
  data = {
   'balance': 'dsfdfdf',
  }
  response = self.client.patch(reverse('ledger-detail', kwargs={'pk': ledger.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_patch_invalid_balance_max(self):
  ledger = Ledger.objects.last()
  data = {
   'balance': 1000000000000000000000000.0 * 1000000000000
  }
  response = self.client.patch(reverse('ledger-detail', kwargs={'pk': ledger.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_patch_invalid_currency_str(self):
  ledger = Ledger.objects.last()

  data = {
   'currency': 'sfsafasd',
  }
  response = self.client.patch(reverse('ledger-detail', kwargs={'pk': ledger.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class PhoneRentViewTest(APITransactionTestCase):
 url = reverse('phonerent-list')
 is_logined = False
 need_admin_user = True

 def setUp(self):
  PhoneRent.objects.create(
   name='sms activate', url='https://sms-activate.ru/',
   note='some note', rent_service_type=PhoneRent.SMSService.SMS_ACTIVATE
  )
  create_test_users()

  if not self.is_logined:
   self.client.login(
    username=settings.ANON_APP_TEST_USER_NAME if not self.need_admin_user
    else settings.ANON_APP_TEST_SUPERUSER_NAME,
    password=settings.ANON_APP_TEST_USER_PASSWORD if not self.need_admin_user
    else settings.ANON_APP_TEST_SUPERUSER_PASSWORD,
   )
   self.is_logined = True

 def test_list(self):
  response = self.client.get(self.url)
  self.assertEqual(PhoneRent.objects.count(), len(response.data['results']))
  self.assertEqual(PhoneRent.objects.get().name, 'sms activate')
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')

 def test_valid_create(self):
  data = {
   'name': 'sms activate 2', 'url': 'https://sms-activate.ru', 'note': 'service about sms',
   'rent_service_type': PhoneRent.SMSService.SMS_ACTIVATE.value
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_201_CREATED)
  self.assertEqual(PhoneRent.objects.count(), 2)
  self.assertEqual(PhoneRent.objects.get(name='sms activate 2').name, 'sms activate 2')

 def test_invalid_create(self):
  data = {'name': 'invalid_service', 'url': 'fsjfhsfjsadfsdsahdsdjfs'}
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(PhoneRent.objects.count(), 1)

 def test_put_valid(self):
  phone_rent = PhoneRent.objects.first()
  data = {
   'name': 'drf', 'url': 'https://not-sms-activate.ru/', 'note': '',
   'rent_service_type': PhoneRent.SMSService.SMS_MAN.value
  }
  response = self.client.put(reverse('phonerent-detail', kwargs={'pk': phone_rent.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual(response.data, data)

 def test_put_invalid(self):
  phone_rent = PhoneRent.objects.first()
  data = {
   'name': 'sdasdas', 'url': '53fgdg4333333', 'note': 'gdfdsddf',
   'rent_service_type': 'fdfsdfsdfsdfds'}
  response = self.client.put(reverse('phonerent-detail', kwargs={'pk': phone_rent.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_put_invalid_note_none(self):
  phone_rent = PhoneRent.objects.first()
  data = {'name': 'github', 'url': 'https://github.com/', 'note': None}
  response = self.client.put(reverse('phonerent-detail', kwargs={'pk': phone_rent.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_put_invalid_rent_type_none(self):
  phone_rent = PhoneRent.objects.first()
  data = {
   'name': 'github', 'url': 'https://github.com/', 'note': 'dffdf',
   'rent_service_type': None
  }
  response = self.client.put(reverse('phonerent-detail', kwargs={'pk': phone_rent.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_put_invalid_none(self):
  phone_rent = PhoneRent.objects.first()
  data = {'name': None, 'url': None, 'note': None, 'rent_service_type': None}
  response = self.client.put(reverse('phonerent-detail', kwargs={'pk': phone_rent.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertIsNotNone(phone_rent.name)

 def test_patch_valid(self):
  phone_rent = PhoneRent.objects.first()
  data = {'name': 'I\'m changed'}
  response = self.client.patch(reverse('phonerent-detail', kwargs={'pk': phone_rent.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual(response.data['name'], data['name'])

 def test_patch_invalid(self):
  phone_rent = PhoneRent.objects.first()
  data = {'url': 'invalid_url'}
  response = self.client.patch(reverse('phonerent-detail', kwargs={'pk': phone_rent.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_patch_invalid_none(self):
  phone_rent = PhoneRent.objects.first()
  data = {'name': None}
  response = self.client.patch(reverse('phonerent-detail', kwargs={'pk': phone_rent.pk}), data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertIsNotNone(phone_rent.name)


class PhoneRentAccountViewTest(APITransactionTestCase):
 url = reverse('phonerentaccount-list')
 is_logined = False
 need_admin_user = True

 def setUp(self):
  phone_rent = PhoneRent.objects.create(
   name='sms activate', url='https://sms-activate.ru/',
   note='some note', rent_service_type=PhoneRent.SMSService.SMS_ACTIVATE
  )
  PhoneRentAccount.objects.create(username='tifox', password='qwerty', api_key='qqq', service=phone_rent)
  create_test_users()

  if not self.is_logined:
   self.client.login(
    username=settings.ANON_APP_TEST_USER_NAME if not self.need_admin_user
    else settings.ANON_APP_TEST_SUPERUSER_NAME,
    password=settings.ANON_APP_TEST_USER_PASSWORD if not self.need_admin_user
    else settings.ANON_APP_TEST_SUPERUSER_PASSWORD,
   )
   self.is_logined = True
  self.request_factory = APIRequestFactory()

 def test_list(self):
  response = self.client.get(self.url)
  self.assertEqual(PhoneRentAccount.objects.count(), len(response.data['results']))
  self.assertEqual(PhoneRentAccount.objects.get().username, 'tifox')
  self.assertEqual(response.status_code, status.HTTP_200_OK, msg=f'Response: {response.data}')

 def test_valid_create(self):
  phone_rent = PhoneRent.objects.last()
  data = {'username': 'bitcoin_magnate', 'password': 'sdsdasdsad', 'api_key': 'qweqwe',
    'service': reverse('phonerent-detail', kwargs={'pk': phone_rent.pk})}
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_201_CREATED)
  self.assertEqual(PhoneRentAccount.objects.count(), 2)
  self.assertEqual(PhoneRentAccount.objects.get(username='bitcoin_magnate').username, 'bitcoin_magnate')

 def test_invalid_username_create(self):
  phone_rent = PhoneRent.objects.last()
  data = {
   'username': None, 'password': 'sdsdasdsad', 'api_key': 'qweqwe',
   'service': reverse('phonerent-detail', kwargs={'pk': phone_rent.pk})
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(PhoneRentAccount.objects.count(), 1)

 def test_invalid_password_create(self):
  phone_rent = PhoneRent.objects.last()
  data = {
   'username': 'karlen-molodec', 'password': None, 'api_key': 'qweqwe',
   'service': reverse('phonerent-detail', kwargs={'pk': phone_rent.pk})
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(PhoneRentAccount.objects.count(), 1)

 def test_invalid_phone_rent_service_create(self):
  data = {
   'username': 'karlen-molodec', 'password': 'sfsafdsad', 'api_key': 'qweqwe',
   'service': reverse('phonerent-detail', kwargs={'pk': 423213})
  }
  response = self.client.post(self.url, data, format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
  self.assertEqual(PhoneRentAccount.objects.count(), 1)

 def test_put_valid(self):
  phone_rent_account = PhoneRentAccount.objects.first()
  phone_rent = PhoneRent.objects.create(
   name='drf', url='https://www.django-rest-framework.org/api-guide/testing/', note='it can help',
   rent_service_type=PhoneRent.SMSService.SMS_MAN
  )
  data = {
   'username': 'some_user', 'password': 'sdasda', 'api_key': 'some_key',
   'service': reverse('phonerent-detail', kwargs={'pk': phone_rent.pk})
  }
  response = self.client.put(reverse('phonerentaccount-detail', kwargs={'pk': phone_rent_account.pk}), data,
          format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual(response.data['username'], data['username'])
  self.assertEqual(response.data['password'], data['password'])

 def test_put_username_invalid(self):
  phone_rent_account = PhoneRentAccount.objects.first()
  phone_rent = PhoneRent.objects.create(
   name='drf', url='https://www.django-rest-framework.org/api-guide/testing/', note='it can help'
  )
  data = {
   'username': None, 'password': 'sdasda',
   'service': reverse('phonerent-detail', kwargs={'pk': phone_rent.pk})
  }
  response = self.client.put(reverse('phonerentaccount-detail', kwargs={'pk': phone_rent_account.pk}), data,
          format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_put_password_invalid(self):
  phone_rent_account = PhoneRentAccount.objects.first()
  phone_rent = PhoneRent.objects.create(
   name='drf', url='https://www.django-rest-framework.org/api-guide/testing/', note='it can help'
  )
  data = {
   'username': 'sdfasdsad', 'password': None,
   'service': reverse('phonerent-detail', kwargs={'pk': phone_rent.pk})
  }
  response = self.client.put(reverse('phonerentaccount-detail', kwargs={'pk': phone_rent_account.pk}), data,
          format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_put_phone_rent_account_invalid(self):
  phone_rent_account = PhoneRentAccount.objects.first()
  data = {
   'username': 'sdfasdsad',
   'password': '434',
   'service': reverse('phonerent-detail', kwargs={'pk': 32312321})
  }
  response = self.client.put(reverse('phonerentaccount-detail', kwargs={'pk': phone_rent_account.pk}), data,
          format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_patch_valid(self):
  phone_rent_account = PhoneRentAccount.objects.first()
  data = {'username': 'karlen-is-the-best'}
  response = self.client.patch(reverse('phonerentaccount-detail', kwargs={'pk': phone_rent_account.pk}), data,
          format='json')
  self.assertEqual(response.status_code, status.HTTP_200_OK)
  self.assertEqual(response.data['username'], data['username'])

 def test_patch_invalid(self):
  phone_rent_account = PhoneRentAccount.objects.first()
  data = {'username': None}
  response = self.client.patch(reverse('phonerentaccount-detail', kwargs={'pk': phone_rent_account.pk}), data,
          format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

 def test_patch_invalid_max(self):
  phone_rent_account = PhoneRentAccount.objects.first()
  data = {'username': 'python' * 100}
  response = self.client.patch(reverse('phonerentaccount-detail', kwargs={'pk': phone_rent_account.pk}), data,
          format='json')
  self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)