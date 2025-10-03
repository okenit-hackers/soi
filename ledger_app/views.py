import logging

from django.shortcuts import render
from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets, mixins, status
from rest_framework.permissions import IsAuthenticated, IsAdminUser

from ledger_app.models import Ledger, PaidService, Currency, ServiceAccount, PhoneRent, PhoneRentAccount
from ledger_app.serializers import PaidServiceSerializer, CurrencySerializer, ServiceAccountSerializer, \
 LedgerSerializer, PhoneRentSerializer, PhoneRentAccountSerializer

logger = logging.getLogger(__name__)


class PaidServiceViewSet(
 viewsets.ModelViewSet
):
 queryset = PaidService.objects.all()
 serializer_class = PaidServiceSerializer
 permission_classes = (IsAuthenticated,)

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
 def create(self, request, *args, **kwargs):
  create = super(PaidServiceViewSet, self).create(request, *args, **kwargs)
  logger.info(f'Created paid service {create.data.serializer.instance} '
     f'with pk={create.data.serializer.instance.pk}. '
     f'Reason_phrase: {create.reason_phrase}. '
     f'Subject: {request.user}')
  return create

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def update(self, request, *args, **kwargs):
  update = super(PaidServiceViewSet, self).update(request, *args, **kwargs)
  logger.info(f'Updated paid service {update.data.serializer.instance} '
     f'with pk={update.data.serializer.instance.pk}. '
     f'Reason_phrase: {update.reason_phrase}. '
     f'Subject: {request.user}')
  return update

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def partial_update(self, request, *args, **kwargs):
  update = super(PaidServiceViewSet, self).partial_update(request, *args, **kwargs)
  return update

 @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
         status.HTTP_404_NOT_FOUND: "Объект не найден"})
 def destroy(self, request, *args, **kwargs):
  destroy = super(PaidServiceViewSet, self).destroy(request, *args, **kwargs)
  logger.info(f'Deleted paid service with pk={kwargs["pk"]}. Subject: {request.user}')
  return destroy


class CurrencyViewSet(
 viewsets.ModelViewSet
):
 queryset = Currency.objects.all()
 serializer_class = CurrencySerializer
 permission_classes = (IsAuthenticated,)

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
 def create(self, request, *args, **kwargs):
  create = super(CurrencyViewSet, self).create(request, *args, **kwargs)
  logger.info(f'Created currency {create.data.serializer.instance} '
     f'with pk={create.data.serializer.instance.pk}. '
     f'Reason_phrase: {create.reason_phrase}. '
     f'Subject: {request.user}')
  return create

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def update(self, request, *args, **kwargs):
  update = super(CurrencyViewSet, self).update(request, *args, **kwargs)
  logger.info(f'Updated currency {update.data.serializer.instance} '
     f'with pk={update.data.serializer.instance.pk}. '
     f'Reason_phrase: {update.reason_phrase}. '
     f'Subject: {request.user}')
  return update

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def partial_update(self, request, *args, **kwargs):
  update = super(CurrencyViewSet, self).partial_update(request, *args, **kwargs)
  return update

 @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
         status.HTTP_404_NOT_FOUND: "Объект не найден"})
 def destroy(self, request, *args, **kwargs):
  destroy = super(CurrencyViewSet, self).destroy(request, *args, **kwargs)
  logger.info(f'Deleted currency with pk={kwargs["pk"]}. Subject: {request.user}')
  return destroy


class ServiceAccountViewSet(
 viewsets.ModelViewSet
):
 queryset = ServiceAccount.objects.all()
 serializer_class = ServiceAccountSerializer
 permission_classes = (IsAuthenticated,)

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
 def create(self, request, *args, **kwargs):
  create = super(ServiceAccountViewSet, self).create(request, *args, **kwargs)
  logger.info(f'Created service account {create.data.serializer.instance} '
     f'with pk={create.data.serializer.instance.pk}. '
     f'Reason_phrase: {create.reason_phrase}. '
     f'Subject: {request.user}')
  return create

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def update(self, request, *args, **kwargs):
  update = super(ServiceAccountViewSet, self).update(request, *args, **kwargs)
  logger.info(f'Updated service account {update.data.serializer.instance} '
     f'with pk={update.data.serializer.instance.pk}. '
     f'Reason_phrase: {update.reason_phrase}. '
     f'Subject: {request.user}')
  return update

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def partial_update(self, request, *args, **kwargs):
  update = super(ServiceAccountViewSet, self).partial_update(request, *args, **kwargs)
  return update

 @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
         status.HTTP_404_NOT_FOUND: "Объект не найден"})
 def destroy(self, request, *args, **kwargs):
  destroy = super(ServiceAccountViewSet, self).destroy(request, *args, **kwargs)
  logger.info(f'Deleted service account with pk={kwargs["pk"]}. Subject: {request.user}')
  return destroy


class LedgerViewSet(
 viewsets.ModelViewSet
):
 queryset = Ledger.objects.all()
 serializer_class = LedgerSerializer
 permission_classes = (IsAuthenticated,)

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
 def create(self, request, *args, **kwargs):
  create = super(LedgerViewSet, self).create(request, *args, **kwargs)
  logger.info(f'Created ledger {create.data.serializer.instance} '
     f'with pk={create.data.serializer.instance.pk}. '
     f'Reason_phrase: {create.reason_phrase}. '
     f'Subject: {request.user}')
  return create

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def update(self, request, *args, **kwargs):
  update = super(LedgerViewSet, self).update(request, *args, **kwargs)
  logger.info(f'Updated ledger {update.data.serializer.instance} '
     f'with pk={update.data.serializer.instance.pk}. '
     f'Reason_phrase: {update.reason_phrase}. '
     f'Subject: {request.user}')
  return update

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def partial_update(self, request, *args, **kwargs):
  update = super(LedgerViewSet, self).partial_update(request, *args, **kwargs)
  return update

 @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
         status.HTTP_404_NOT_FOUND: "Объект не найден"})
 def destroy(self, request, *args, **kwargs):
  destroy = super(LedgerViewSet, self).destroy(request, *args, **kwargs)
  logger.info(f'Deleted ledger with pk={kwargs["pk"]}. Subject: {request.user}')
  return destroy


class PhoneRentViewSet(viewsets.ModelViewSet):
 queryset = PhoneRent.objects.all()
 serializer_class = PhoneRentSerializer
 permission_classes = (IsAuthenticated, IsAdminUser)

 def list(self, request, *args, **kwargs):
  return super(PhoneRentViewSet, self).list(request, *args, **kwargs)

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
 def create(self, request, *args, **kwargs):
  create = super(PhoneRentViewSet, self).create(request, *args, **kwargs)
  logger.info(f'Created phone_rent {create.data.serializer.instance} '
     f'with pk={create.data.serializer.instance.pk}. '
     f'Reason_phrase: {create.reason_phrase}. '
     f'Subject: {request.user}')
  return create

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def update(self, request, *args, **kwargs):
  update = super(PhoneRentViewSet, self).update(request, *args, **kwargs)
  logger.info(f'Updated phone_rent {update.data.serializer.instance} '
     f'with pk={update.data.serializer.instance.pk}. '
     f'Reason_phrase: {update.reason_phrase}. '
     f'Subject: {request.user}')
  return update

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def partial_update(self, request, *args, **kwargs):
  update = super(PhoneRentViewSet, self).partial_update(request, *args, **kwargs)
  return update

 @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
         status.HTTP_404_NOT_FOUND: "Объект не найден"})
 def destroy(self, request, *args, **kwargs):
  destroy = super(PhoneRentViewSet, self).destroy(request, *args, **kwargs)
  logger.info(f'Deleted phone_rent with pk={kwargs["pk"]}. Subject: {request.user}')
  return destroy


class PhoneRentAccountViewSet(viewsets.ModelViewSet):
 queryset = PhoneRentAccount.objects.all()
 serializer_class = PhoneRentAccountSerializer
 permission_classes = (IsAuthenticated, IsAdminUser)

 def list(self, request, *args, **kwargs):
  return super(PhoneRentAccountViewSet, self).list(request, *args, **kwargs)

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
 def create(self, request, *args, **kwargs):
  create = super(PhoneRentAccountViewSet, self).create(request, *args, **kwargs)
  logger.info(f'Created phone_rent_account {create.data.serializer.instance} '
     f'with pk={create.data.serializer.instance.pk}. '
     f'Reason_phrase: {create.reason_phrase}. '
     f'Subject: {request.user}')
  return create

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def update(self, request, *args, **kwargs):
  update = super(PhoneRentAccountViewSet, self).update(request, *args, **kwargs)
  logger.info(f'Updated phone_rent_account {update.data.serializer.instance} '
     f'with pk={update.data.serializer.instance.pk}. '
     f'Reason_phrase: {update.reason_phrase}. '
     f'Subject: {request.user}')
  return update

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def partial_update(self, request, *args, **kwargs):
  update = super(PhoneRentAccountViewSet, self).partial_update(request, *args, **kwargs)
  return update

 @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
         status.HTTP_404_NOT_FOUND: "Объект не найден"})
 def destroy(self, request, *args, **kwargs):
  destroy = super(PhoneRentAccountViewSet, self).destroy(request, *args, **kwargs)
  logger.info(f'Deleted phone_rent_account with pk={kwargs["pk"]}. Subject: {request.user}')
  return destroy