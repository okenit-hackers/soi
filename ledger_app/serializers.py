from rest_framework import serializers
from ledger_app.models import Ledger, PaidService, Currency, ServiceAccount, PhoneRent, PhoneRentAccount


class PaidServiceSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = PaidService
  fields = ['name', 'url', 'note']


class CurrencySerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = Currency
  fields = ['name', ]


class ServiceAccountSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = ServiceAccount
  fields = ['username', 'password', 'service']


class LedgerSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = Ledger
  fields = ['service', 'currency', 'account', 'balance', ]


class PhoneRentSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = PhoneRent
  fields = ['rent_service_type', 'name', 'url', 'note', ]


class PhoneRentAccountSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = PhoneRentAccount
  fields = ['service', 'api_key', 'username', 'password', 'balance']
