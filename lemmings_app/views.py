import logging
import re
from typing import Union

from celery.app.base import get_current_app as get_current_celery_app
from celery.result import AsyncResult, result_from_tuple
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import render
from django.views.decorators.csrf import csrf_protect
from django_celery_beat.models import IntervalSchedule, CrontabSchedule, SolarSchedule, ClockedSchedule
from drf_yasg.utils import swagger_auto_schema
from rest_framework import viewsets, status, mixins
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.renderers import JSONRenderer, BrowsableAPIRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from lemmings_app.conf import LemmingsAppConf as Conf
from lemmings_app.exceptions import InvalidService
from lemmings_app.forms import ImportBotAccountForm
from lemmings_app.models import LemmingsTask, BotAccount, BehaviorBots
from lemmings_app.permissions import IsAdminOr
from lemmings_app.serializers import LemmingsTaskSerializer, BehaviorBotsSerializer, CrontabScheduleSerializer, \
 IntervalScheduleViewSetSerializer, SolarScheduleSerializer, ClockedScheduleSerializer, BotAccountSerializer
from lemmings_app.utils import validate_lmgs_task, run_celery_task

logger = logging.getLogger(__name__)


class CeleryTaskView(APIView):
 url_prefix = 'celery-task'
 basename = 'celery-task'
 lookup_field = 'celery_task_id'
 permission_classes = (IsAuthenticated, IsAdminOr)
 renderer_classes = [BrowsableAPIRenderer, JSONRenderer]
 http_method_names = ['get', 'delete']

 # noinspection PyMethodMayBeStatic,DuplicatedCode
 def get(self, request: HttpRequest, celery_task_id=None, **kwargs):
  if celery_task_id is None:
   return Response(
    {'error': 400, 'details': 'specify celery task id in url'},
    status=400
   )

  logger.info(f'Try to get meta of celery task {celery_task_id}')

  try:
   app = get_current_celery_app()
   # noinspection PyProtectedMember
   task_meta = app.backend.get_task_meta(str(celery_task_id))
  except Exception as e:
   return Response(
    {'error': 503, 'details': f'Can\'t get task meta [{celery_task_id}]: {e}'},
    status=503
   )

  task_meta['children'] = [result_from_tuple(child, app).id for child in task_meta.get('children', [])]
  task_meta['children_urls'] = [self.build_celery_task_url(request, url) for url in task_meta['children']]
  if isinstance(task_meta['result'], BaseException):
   task_meta['result'] = task_meta['result'].__repr__().replace(r'\'', '\'')

  for field in ('task_id', 'parent_id'):
   task_meta[f'{field}_url'] = self.build_celery_task_url(request, task_meta.get(field))

  return Response(task_meta)

 # noinspection DuplicatedCode,PyMethodMayBeStatic
 def delete(self, request: HttpRequest, celery_task_id=None, **kwargs):
  if celery_task_id is None:
   return Response(
    {'error': 400, 'details': 'specify celery task id in url'},
    status=400
   )

  logger.info(f'Try to revoke celery task {celery_task_id}')

  try:
   AsyncResult(celery_task_id).revoke(terminate=True)
  except Exception as e:
   return Response(
    {'error': 503, 'details': f'Can\'t revoke task [{celery_task_id}]: {e}'},
    status=503
   )

  return Response({'ok': True})

 @staticmethod
 def build_celery_task_url(request: HttpRequest, task_id: Union[str, None]) -> Union[str, None]:
  if task_id is None:
   return None

  path = re.sub(Conf.CELERY_TASK_REGEX, task_id, request.get_full_path())

  # noinspection PyProtectedMember
  return f'{request.scheme}://{request._get_raw_host()}{path}'


class LemmingsTaskViewSet(
 viewsets.ReadOnlyModelViewSet,
 mixins.CreateModelMixin
):
 """
 Позволяет обрабатывать запросы запуска задач взаимодействия с ботами.
 Запускает задачи создания/авторизации ботов.

 Доступно только аутентифицированным администраторам.
 """
 url_prefix = 'lmgs_task'
 queryset = LemmingsTask.objects.all()
 serializer_class = LemmingsTaskSerializer
 permission_classes = (IsAuthenticated, IsAdminUser)

 # todo: добавить эндпоинт для очистки неуспешных задач

 def create(self, request, *args, **kwargs):
  """
  Создаёт запрос на создание задачи.

  В случае успешной обработки запускает задачу создания/авторизации ботов.


  Пример запроса:

   POST http://127.0.0.1:8000/lmgs_task/?format=json
   Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
   Content-Type: application/json

   {
    "action": "create_vk_bot",
    "kwargs": {},
    "chain": "http://127.0.0.1:8000/chain/1/"
   }

  Пример ответа:

   {
    "url": "http://127.0.0.1:8000/lmgs_task/2/",
    "action": "create_vk_bot",
    "kwargs": {},
    "task_id": "326173df-440f-4c56-9a08-f426ef0276af",
    "chain": "http://127.0.0.1:8000/chain/1/"
   }

  Инициатор ПК COC-А отправляет POST запрос в кодировке UTF-8. В теле запроса должен содержаться JSON
  c следующими полями:

  * "action" - действие задачи, одно из:
   - create_vk_bot
   - create_twitter_bot
   - create_instagram_bot
   - login_vk_bot
   - login_twitter_bot
   - login_instagram_bot
  * "kwargs" - словарь с переданные в задачу аргументами, для задачи создания бота можно, но не обязательно,
   указать "init_data" - эта информация будет использована для заполнения профиля, для задачи авторизации
   [todo: расписать что можно передать в init_data и реализовать использование этих данных]
   необходимо передать параметры "username" (уникальный идентификатор аккаунта бота в рамках сервиса),
   "password" и "phone_number" (номер телефона с +)
  * "chain" - ссылка на ассоциированную цепочку анонимизации.

  К ответу добавляются следующие поля:

  * "url" - ссылка на объект запроса создания задачи сбора;
  * "task_id" - id задачи для получения результата из celery backend;;

  В случае успешного создания задачи возвращается ответ со статусом 201 (Created)

  В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
  JSON с указанием поля, которое было заполнено неверно.

  В случае, если пользователь не имеет доступа к тому или иному указанному значению, возвращается
  ответ со статусом 403 (Forbidden). В теле содержится JSON, в поле "details" которого содержится
  информация об ошибке.
  """

  serializer = self.get_serializer(data=request.data)
  serializer.is_valid(raise_exception=True)
  with transaction.atomic():
   lmgs_task_instance = serializer.save()
   try:
    validate_lmgs_task(lmgs_task_instance)
   except Exception as e:
    logger.error(e, exc_info=True)
    raise e

  run_celery_task(lmgs_task_instance)

  headers = self.get_success_headers(serializer.data)
  logger.info(f'Created lemming task {serializer.data} '
     f'with pk={serializer.instance.pk}. '
     f'Reason_phrase: Created. '
     f'Subject: {request.user}')
  return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

 def list(self, request, *args, **kwargs):
  """
  Возвращает список принятых запросов запуска задач создания/авторизации ботов.

  Пример запроса:

   GET http://127.0.0.1:8000/lmgs_task/?format=json
   Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2


  Пример ответа:

   [
    {
     "url": "http://127.0.0.1:8000/lmgs_task/2/",
     "action": "create_vk_bot",
     "kwargs": {},
     "task_id": "326173df-440f-4c56-9a08-f426ef0276af",
     "chain": "http://127.0.0.1:8000/chain/1/"
    }
   ]

  Инициатор отправляет GET запрос в кодировке UTF-8.

  В случае успеха возвращается ответ со статусом 200 (OK). В теле ответа содержится JSON список объектов,
  содержащие следующие поля:
  * "url" - ссылка на объект;
  * "action" - действие задачи;
  * "kwargs" - словарь с переданные в задачу аргументами;
  * "task_id" - id задачи для получения результата из celery backend;
  * "chain" - ссылка на ассоциированную цепочку анонимизации.

  В случае, если была запрошена отсутствующая страница, возвращается ответ со статусом 404 (Not Found).

  В случае, если пользователь не имеет доступа к тому или иному указанному значению, возвращается
  ответ со статусом 403 (Forbidden). В теле содержится JSON, в поле "details" которого содержится
  информация об ошибке.
  """

  return super(LemmingsTaskViewSet, self).list(request, *args, **kwargs)

 def retrieve(self, request, *args, **kwargs):
  """
  Возвращает информацию о принятом запросов запуска задач создания/авторизации ботов.

  Пример запроса:

   GET http://127.0.0.1:8000/lmgs_task/2/?format=json
   Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2


  Пример ответа:

   {
    "url": "http://127.0.0.1:8000/lmgs_task/2/",
    "action": "create_vk_bot",
    "kwargs": {},
    "task_id": "326173df-440f-4c56-9a08-f426ef0276af",
    "chain": "http://127.0.0.1:8000/chain/1/"
   }

  Инициатор отправляет GET запрос в кодировке UTF-8.

  В случае успеха возвращается ответ со статусом 200 (OK). В теле ответа содержится JSON объект,
  содержащий следующие поля:
  * "url" - ссылка на объект;
  * "action" - действие задачи;
  * "kwargs" - словарь с переданные в задачу аргументами;
  * "task_id" - id задачи для получения результата из celery backend;
  * "chain" - ссылка на ассоциированную цепочку анонимизации.

  В случае, если была запрошена отсутствующая страница, возвращается ответ со статусом 404 (Not Found).

  В случае, если пользователь не имеет доступа к тому или иному указанному значению, возвращается
  ответ со статусом 403 (Forbidden). В теле содержится JSON, в поле "details" которого содержится
  информация об ошибке.
  """

  return super(LemmingsTaskViewSet, self).retrieve(request, *args, **kwargs)


@csrf_protect
def import_bots(request: HttpRequest):
 if not request.user or not request.user.is_staff:
  return HttpResponseForbidden('You are not staff.')

 form = ImportBotAccountForm()

 ctx = {
  'form': form,
  'cl': {'opts': BotAccount._meta},
  'app_label': 'lemmings_app'
 }

 if request.method == 'GET':
  return render(request, 'admin/import_bots.html', ctx)
 elif request.method == 'POST':
  form = ImportBotAccountForm(request.POST, request.FILES)
  if form.is_valid():
   try:
    form.save()
   except InvalidService as e:
    form.add_error('file', error=ValidationError(f'Неизвестный сервис {e.detail[0]}'))
    logger.warning(f'Неизвестный сервис {e}', exc_info=True)
    ctx = {
     'form': form,
     'cl': {'opts': BotAccount._meta},
     'app_label': 'lemmings_app'
    }
    return render(request, 'admin/import_bots.html', ctx)
   except ValueError:
    form.add_error('file', error=ValidationError(f'Проверьте корректность формата файла'))
    logger.warning(f'Проверьте корректность формата файла', exc_info=True)
    ctx = {
     'form': form,
     'cl': {'opts': BotAccount._meta},
     'app_label': 'lemmings_app'
    }
    return render(request, 'admin/import_bots.html', ctx)
   except Exception as e:
    form.add_error('file', error=ValidationError(f'Произошла ошибка при импорте файла: {e}'))
    logger.warning(f'Произошла ошибка при импорте файла: {e}', exc_info=True)
    ctx = {
     'form': form,
     'cl': {'opts': BotAccount._meta},
     'app_label': 'lemmings_app'
    }
    return render(request, 'admin/import_bots.html', ctx)
   return HttpResponseRedirect('../')
  return HttpResponseRedirect('../')


class BotAccountViewSet(viewsets.ModelViewSet):
 """
 Позволяет просмотреть информацию об аккаунтах ботов.

 Доступно только аутентифицированным пользователям.
 """
 queryset = BotAccount.objects.all()
 serializer_class = BotAccountSerializer

 permission_classes = (IsAuthenticated,)

 def get_queryset(self):
  if self.request.user.is_superuser:
   return BotAccount.objects.all()
  else:
   return BotAccount.objects.filter(internet_resources__tasktype__users=self.request.user).distinct()

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
 def list(self, request, *args, **kwargs):
  """
  Возвращает список аккаунтов ботов

  Пример запроса:

   GET http://127.0.0.1:8000/botaccount
   Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

  Пример ответа:
   {
    "id": 140,
    "service": "CLASSMATES",
    "username": "590001116621",
    "phone_number": "+79093731439",
    "chain_id": 4,
    "created": "2023-07-28T11:12:16.152395+03:00",
    "changed": "2023-07-28T11:12:18.906022+03:00",
    "service_account": false
  }

  Инициатор ПК COC-А отправляет GET запрос в кодировке UTF-8.

  В случае успеха возвращается ответ со статусом 200 (OK). В теле ответа содержится JSON, содержащий следующие
  поля:
  * "count" - содержит количество элементов;
  * "next" - содержит ссылку для загрузки следующей порции результатов или null;
  * "previous" - содержит ссылку для загрузки предыдущей порции результатов или null;
  * "results" - список объектов типов задач.

  В случае, если была запрошена отсутствующая страница, возвращается ответ со статусом 404 (Not Found).

  """
  return super(BotAccountViewSet, self).list(request, *args, **kwargs)


class BehaviorBotsViewSet(
 viewsets.ModelViewSet
):
 queryset = BehaviorBots.objects.all()
 serializer_class = BehaviorBotsSerializer
 permission_classes = (IsAuthenticated,)

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
 def create(self, request, *args, **kwargs):
  create = super(BehaviorBotsViewSet, self).create(request, *args, **kwargs)
  logger.info(f'Created behavior_bots {create.data.serializer.instance} '
     f'with pk={create.data.serializer.instance.pk}. '
     f'Reason_phrase: {create.reason_phrase}. '
     f'Subject: {request.user}')
  return create

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def update(self, request, *args, **kwargs):
  update = super(BehaviorBotsViewSet, self).update(request, *args, **kwargs)
  logger.info(f'Updated behavior_bots {update.data.serializer.instance} '
     f'with pk={update.data.serializer.instance.pk}. '
     f'Reason_phrase: {update.reason_phrase}. '
     f'Subject: {request.user}')
  return update

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def partial_update(self, request, *args, **kwargs):
  update = super(BehaviorBotsViewSet, self).partial_update(request, *args, **kwargs)
  return update

 @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
         status.HTTP_404_NOT_FOUND: "Объект не найден"})
 def destroy(self, request, *args, **kwargs):
  destroy = super(BehaviorBotsViewSet, self).destroy(request, *args, **kwargs)
  logger.info(f'Deleted behavior_bots with pk={kwargs["pk"]}. Subject: {request.user}')
  return destroy


class IntervalScheduleViewSet(viewsets.ModelViewSet):
 """
 Позволяет работать с типами задач.

 Доступно только аутентифицированным пользователям.
 """
 queryset = IntervalSchedule.objects.all()
 serializer_class = IntervalScheduleViewSetSerializer
 permission_classes = (IsAuthenticated,)
 filterset_fields = '__all__'

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
 def create(self, request, *args, **kwargs):
  create = super(IntervalScheduleViewSet, self).create(request, *args, **kwargs)
  logger.info(f'Created interval schedule {create.data.serializer.instance} '
     f'with pk={create.data.serializer.instance.pk}. '
     f'Reason_phrase: {create.reason_phrase}. '
     f'Subject: {request.user}')
  return create

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def update(self, request, *args, **kwargs):
  update = super(IntervalScheduleViewSet, self).update(request, *args, **kwargs)
  logger.info(f'Updated interval schedule {update.data.serializer.instance} '
     f'with pk={update.data.serializer.instance.pk}. '
     f'Reason_phrase: {update.reason_phrase}. '
     f'Subject: {request.user}')
  return update

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def partial_update(self, request, *args, **kwargs):
  update = super(IntervalScheduleViewSet, self).partial_update(request, *args, **kwargs)
  return update

 @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
         status.HTTP_404_NOT_FOUND: "Объект не найден"})
 def destroy(self, request, *args, **kwargs):
  destroy = super(IntervalScheduleViewSet, self).destroy(request, *args, **kwargs)
  logger.info(f'Deleted interval schedule with pk={kwargs["pk"]}. Subject: {request.user}')
  return destroy


class CrontabScheduleViewSet(viewsets.ModelViewSet):
 """
 Для работы с расписаниями в стиле Crontab
 """
 queryset = CrontabSchedule.objects.all()
 serializer_class = CrontabScheduleSerializer
 permission_classes = (IsAuthenticated,)

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
 def create(self, request, *args, **kwargs):
  create = super(CrontabScheduleViewSet, self).create(request, *args, **kwargs)
  logger.info(f'Created crontab schedule {create.data.serializer.instance} '
     f'with pk={create.data.serializer.instance.pk}. '
     f'Reason_phrase: {create.reason_phrase}. '
     f'Subject: {request.user}')
  return create

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def update(self, request, *args, **kwargs):
  update = super(CrontabScheduleViewSet, self).update(request, *args, **kwargs)
  logger.info(f'Updated crontab schedule {update.data.serializer.instance} '
     f'with pk={update.data.serializer.instance.pk}. '
     f'Reason_phrase: {update.reason_phrase}. '
     f'Subject: {request.user}')
  return update

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def partial_update(self, request, *args, **kwargs):
  update = super(CrontabScheduleViewSet, self).partial_update(request, *args, **kwargs)
  return update

 @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
         status.HTTP_404_NOT_FOUND: "Объект не найден"})
 def destroy(self, request, *args, **kwargs):
  destroy = super(CrontabScheduleViewSet, self).destroy(request, *args, **kwargs)
  logger.info(f'Deleted crontab schedule with pk={kwargs["pk"]}. Subject: {request.user}')
  return destroy


class SolarScheduleViewSet(viewsets.ModelViewSet):
 """
 Для работы с солнечными событиями
 """
 queryset = SolarSchedule.objects.all()
 serializer_class = SolarScheduleSerializer
 permission_classes = (IsAuthenticated,)

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
 def create(self, request, *args, **kwargs):
  create = super(SolarScheduleViewSet, self).create(request, *args, **kwargs)
  logger.info(f'Created solar schedule {create.data.serializer.instance} '
     f'with pk={create.data.serializer.instance.pk}. '
     f'Reason_phrase: {create.reason_phrase}. '
     f'Subject: {request.user}')
  return create

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def update(self, request, *args, **kwargs):
  update = super(SolarScheduleViewSet, self).update(request, *args, **kwargs)
  logger.info(f'Updated solar schedule {update.data.serializer.instance} '
     f'with pk={update.data.serializer.instance.pk}. '
     f'Reason_phrase: {update.reason_phrase}. '
     f'Subject: {request.user}')
  return update

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def partial_update(self, request, *args, **kwargs):
  update = super(SolarScheduleViewSet, self).partial_update(request, *args, **kwargs)
  return update

 @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
         status.HTTP_404_NOT_FOUND: "Объект не найден"})
 def destroy(self, request, *args, **kwargs):
  destroy = super(SolarScheduleViewSet, self).destroy(request, *args, **kwargs)
  logger.info(f'Deleted solar schedule with pk={kwargs["pk"]}. Subject: {request.user}')
  return destroy


class ClockedScheduleViewSet(viewsets.ModelViewSet):
 """
 Для работы с часовыми событиями
 """
 queryset = ClockedSchedule.objects.all()
 serializer_class = ClockedScheduleSerializer
 permission_classes = (IsAuthenticated,)

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
 def create(self, request, *args, **kwargs):
  create = super(ClockedScheduleViewSet, self).create(request, *args, **kwargs)
  logger.info(f'Created clocked schedule {create.data.serializer.instance} '
     f'with pk={create.data.serializer.instance.pk}. '
     f'Reason_phrase: {create.reason_phrase}. '
     f'Subject: {request.user}')
  return create

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def update(self, request, *args, **kwargs):
  update = super(ClockedScheduleViewSet, self).update(request, *args, **kwargs)
  logger.info(f'Updated clocked schedule {update.data.serializer.instance} '
     f'with pk={update.data.serializer.instance.pk}. '
     f'Reason_phrase: {update.reason_phrase}. '
     f'Subject: {request.user}')
  return update

 @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
         status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
 def partial_update(self, request, *args, **kwargs):
  update = super(ClockedScheduleViewSet, self).partial_update(request, *args, **kwargs)
  return update

 @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
         status.HTTP_404_NOT_FOUND: "Объект не найден"})
 def destroy(self, request, *args, **kwargs):
  destroy = super(ClockedScheduleViewSet, self).destroy(request, *args, **kwargs)
  logger.info(f'Deleted clocked schedule with pk={kwargs["pk"]}. Subject: {request.user}')
  return destroy