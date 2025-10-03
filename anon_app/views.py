import logging
import time
from urllib.parse import urlparse

from celery import chain as celery_chain
from celery.result import AsyncResult
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.http import HttpRequest, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import get_resolver
from django.views.decorators.csrf import csrf_protect
from drf_yasg.utils import swagger_auto_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.request import Request
from rest_framework.views import APIView, Response

from anon_app.admin import ChainAdmin
from anon_app.forms import ImportProxiesForm
from anon_app.models import AppImage, Chain, Edge, Hosting, HostingAccount, Node, Proxy, Server, SrvAccount
from anon_app.serializers import (AppImageSerializer, ChainRebuildSerializer, ChainSerializer, ChainsRebuildSerializer,
                                  EdgeSerializer, HostingAccountSerializer, HostingSerializer, NodeSerializer,
                                  ProxySerializer, ServerAccountSerializer, ServerSerializer)
from anon_app.tasks.tasks import (build_chain, callback_build_chain_errors, check_chain_status,
                                  forward_ports_to_priority_celery_queue_after_building,
                                  kill_processes,
                                  post_build_chain, pre_build_chain)

logger = logging.getLogger(__name__)

ENTRY_SERVER_INDEX = 1
WAIT_TIME_BETWEEN_CHECKING_CELERY_TASK_RESULT = 0.5


def start_chain_checking(request: Request, chain_id):
    try:
        chain = Chain.objects.get(id=chain_id)
    except Chain.DoesNotExist:
        return Response({
            'ok': False,
            'msg': f'Chain not found [{chain_id}]'
        }, status=404)

    if chain.status not in (Chain.StatusChoice.READY, Chain.StatusChoice.DIED):
        return Response({
            'ok': False,
            'msg': f'Can\'t test chain when its status is {chain.status} [{chain_id}]'
        }, status=400)

    status_replace_with = {
        Chain.StatusChoice.READY: Chain.StatusChoice.TEST_FROM_READY,
        Chain.StatusChoice.DIED: Chain.StatusChoice.TEST_FROM_DIED
    }[chain.status]

    chain.status = status_replace_with
    chain.save(update_fields=['status'])

    result = check_chain_status.delay(
        chain_id=chain_id, is_internal=True,
        task_identifier=f'check:chain:{chain_id}'
    )

    chain.last_checking_celery_task_id = result.id
    chain.save(update_fields=['last_checking_celery_task_id'])

    return Response({
        'ok': True,
        'msg': f'Chain testing started: {chain_id} [{result.id}]'
    })


def revoke_chain_checking(request: Request, chain_id):
    try:
        chain = Chain.objects.get(id=chain_id)
    except Chain.DoesNotExist:
        return Response({
            'ok': False,
            'msg': f'Chain not found [{chain_id}]'
        }, status=404)

    status_replace_with = {
        Chain.StatusChoice.TEST_FROM_READY: Chain.StatusChoice.READY,
        Chain.StatusChoice.TEST_FROM_DIED: Chain.StatusChoice.DIED
    }.get(chain.status)

    if status_replace_with is None:
        return Response({
            'ok': False,
            'msg': f'Chain status is not test: {chain.status} [{chain_id}]'
        }, status=400)

    chain.status = status_replace_with
    chain.save(update_fields=['status'])

    try:
        AsyncResult(str(chain.last_checking_celery_task_id)).revoke(terminate=True)
        return Response({
            'ok': True,
            'msg': f'{chain_id} chain testing revoked [{chain.last_checking_celery_task_id}]'
        })
    except Exception as e:
        logger.error(e, exc_info=True)
        return Response({
            'ok': False,
            'msg': f'Can not revoke chain testing [{chain.last_checking_celery_task_id}]'
        })


@api_view(http_method_names=['POST', 'DELETE'])
@permission_classes(permission_classes=[IsAuthenticated, IsAdminUser])
def chain_checking(request: Request, chain_id):
    if request.method == 'DELETE':
        return revoke_chain_checking(request, chain_id)
    elif request.method == 'POST':
        return start_chain_checking(request, chain_id)

    return Response({
        'ok': False,
        'msg': f'unsupported http method: {request.method.lowercase()}'
    }, status=400)


class ChainView(viewsets.ModelViewSet):
    """
    Позволяет работать с цепочками анонимизации.

    Доступно только аутентифицированным пользователям.
    """

    queryset = Chain.objects.all()
    serializer_class = ChainSerializer
    permission_classes = (IsAuthenticated, IsAdminUser)

    filterset_fields = ['title', 'task_queue_name', 'app_image', 'status', 'app_image__name', 'app_image__title']
    search_fields = ['title', 'task_queue_name', 'app_image__name', 'app_image__title']

    @action(methods=['post'], url_path='rebuild', detail=False, serializer_class=ChainRebuildSerializer)
    def rebuild_chain(self, request):
        """Запускает перестроение выбранной цепочки.

        Пример запроса:
            POST http://127.0.0.1:8000/chain/rebuild/
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "id": 1,
              "with_image": True
            }

            "with_image" - Для перестроения с обновлением образа. Опциональное поле. По дефолту False.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with_image = serializer.validated_data['with_image']
        chain_id = serializer.validated_data['id']

        chain = get_object_or_404(Chain, id=chain_id)
        self.rebuild(chain_id, with_image)
        return Response(
            {'Запущено обновление цепочки': chain.title, 'with_image': with_image}, status=status.HTTP_200_OK,
        )

    @action(methods=['post'], url_path='rebuild_all', detail=False, serializer_class=ChainsRebuildSerializer)
    def rebuild_chains(self, request):
        """Запускает перестроение всех цепочек.

        Пример запроса:
            POST http://127.0.0.1:8000/chain/rebuild_all/
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "with_image": true
            }

            "with_image" - Для перестроения с обновлением образа. Опциональное поле. По дефолту False.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with_image = serializer.validated_data['with_image']

        chains = Chain.objects.values_list('id', 'title')
        for chain in chains:
            self.rebuild(chain[0], with_image)
        return Response({
            'Запущено обновление цепочек': [chain[1] for chain in chains],
            'with_image': with_image
        }, status=status.HTTP_200_OK)

    @staticmethod
    def rebuild(chain_id: int, with_image: bool):
        """Функция запускает перестроение цепочки с образом или без."""
        chain = Chain.objects.get(id=chain_id)
        if with_image:
            ChainAdmin.rebuild_chain_with_update_image(chain)
        else:
            ChainAdmin.rebuild_chain(chain)

    def get_queryset(self):
        return self.queryset.exclude(status=Chain.StatusChoice.BLOCK)

    def perform_create(self, serializer):
        with transaction.atomic():
            # todo validate_servers
            chain = serializer.save()
            self.validate(chain)
            # create_available_lmgs_tasks(chain)    # todo remove this

        pre_build_chain_signature = pre_build_chain.s(
            chain_id=chain.id,
            chain_status=Chain.StatusChoice.CREATING,
            is_internal=True,
            task_identifier=f'pre_build:chain:{chain.id}',
        )
        build_chain_signature = build_chain.s(
            chain_id=chain.id,
            is_internal=True,
            task_identifier=f'build:chain:{chain.id}',
        )
        set_up_priority_celery_queue_signature = forward_ports_to_priority_celery_queue_after_building.s(
            chain_id=chain.id,
            is_priority=True,
            task_identifier=f'set_up_priority_celery_queue:chain:{chain.id}',
            is_internal=True,
        )
        post_build_chain_signature = post_build_chain.s(
            chain_id=chain.id,
            msg='Цепочка {}, была успешно построена',
            is_internal=True,
            task_identifier=f'post_build:chain:{chain.id}',
        )
        build_chain_tasks = celery_chain(
            pre_build_chain_signature,
            build_chain_signature,
            set_up_priority_celery_queue_signature,
            post_build_chain_signature,
        )
        build_chain_tasks.apply_async(
            link_error=callback_build_chain_errors.s(chain_id=chain.id),
        )

    def perform_update(self, serializer):
        with transaction.atomic():
            chain = serializer.save()
            self.validate(chain)

    @classmethod
    def validate(cls, chain: Chain):
        chain.validate()

    def create(self, request, *args, **kwargs):
        """
        Добавляет цепочку анонимизации и создает для нее всех доступных ботов.

        Пример запроса:

            POST http://127.0.0.1:8000/chain/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "title":"chain-name",
              "edges": [
                {
                  "in_node": "http://127.0.0.1:8000/node/8/?format=json",
                  "out_node": "http://127.0.0.1:8000/node/9/?format=json",
                  "protocol": "ssh"
                }, {
                  "in_node": "http://127.0.0.1:8000/node/7/?format=json",
                  "out_node": "http://127.0.0.1:8000/node/8/?format=json",
                  "protocol": "ssh"
                }
              ]
            }

        Пример ответа:

            {
              "url":"http://127.0.0.1:8000/chain/8/?format=json",
              "title":"chain-name",
              "edges": [
                {
                  "url": "http://127.0.0.1:8000/edge/1/?format=json",
                  "in_node": "http://127.0.0.1:8000/node/8/?format=json",
                  "out_node": "http://127.0.0.1:8000/node/9/?format=json",
                  "protocol": "ssh"
                }, {
                  "url": "http://127.0.0.1:8000/edge/2/?format=json",
                  "in_node": "http://127.0.0.1:8000/node/7/?format=json",
                  "out_node": "http://127.0.0.1:8000/node/8/?format=json",
                  "protocol": "ssh"
                }
              ]
            }

        Инициатор отправляет POST запрос в кодировке UTF-8. В теле запроса должен содержаться JSON
        c обязательными полями:

        * "title" - название цепочки;
        * "edges" - спсиок объектов соединений;

        Объекты соединений должны содержать в себе поля:

        * "url" - URL объекта (может использоваться для указания взаимосвязи объектов);
        * "in_node" - URL объекта узла-источник;
        * "out_node" - URL объекта узла-приемник;
        * "protocol" - протокол взаимосвязи;

        Определены следющие возможноые значения, которые может принимать поле "protocol":

        * ssh - ssh протокол;

        В случае успешного создания цепочки анонимизации возвращается ответ со статусом 201 (Created)

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.

        Пример запроса с ошибкой:

            POST http://127.0.0.1:8000/chain/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "title": "chain without edges"
            }

        Пример ответа на запрос с ошибкой:

            {
              "edges": [
                "This field is required."
              ]
            }

        В данном случае в запросе было указано ранее используемое имя интернет-ресурса и выбранное значение
        поля "resource_type" не принадлежало множеству допустимых значени.
        """
        request = self.server2node(request)

        create = super(ChainView, self).create(request, *args, **kwargs)

        chain_id = create.data.serializer.instance.pk
        logger.info(
            f'Created chain {create.data["title"]} '
            f'with pk={chain_id}. '
            f'Reason_phrase: {create.reason_phrase}. '
            f'Subject: {request.user}'
        )
        chain = Chain.objects.get(pk=chain_id)
        nodes = chain.sorted_nodes
        for index, node in enumerate(nodes, start=1):
            if index == ENTRY_SERVER_INDEX:
                node.server.type = Server.ENTRY
            elif index == len(nodes):
                node.server.type = Server.OUTPUT
            else:
                node.server.type = Server.INTERMEDIATE
            node.server.anonymization_chain = Chain.objects.get(pk=chain_id)
            node.server.save(update_fields=['type', 'anonymization_chain'])
        return create

    @transaction.atomic
    def server2node(self, request):
        # заменяет в реквесте сервера на ноды, предварительно создавая эти ноды

        server_links = {edge['out_node'] for edge in request.data['edges']}
        server_links |= {edge['in_node'] for edge in request.data['edges']}

        resolver = get_resolver()
        nodes = {}

        for server_link in server_links:
            srv = Server.objects.get(pk=resolver.resolve(urlparse(server_link).path).kwargs['pk'])

            try:
                node = Node.objects.get(server=srv)
            except Node.DoesNotExist as _:
                node = Node.objects.create(**Node.default_dict(srv))

            # qs = Edge.objects.filter(Q(in_node=node) | Q(out_node=node))
            # if not is_new and qs.exists():
            #     in_use = list(qs.values('id', 'chain_id'))
            #     raise ValidationError({
            #         'error': {
            #             'code': 3029,
            #             'description': f'Данный узел уже используется в следующих соединениях: {in_use}'
            #         }
            #     })  todo: musthave
            #  "не использовать в разных цепочках одни и  те же узлы"

            node_drf_instance = NodeSerializer(instance=node, context={'request': request}).data
            nodes[server_link] = node_drf_instance

        for edge in request.data['edges']:
            # noinspection PyProtectedMember
            out_node_link = urlparse(nodes[edge['out_node']]['url'])._replace(query='format=json')
            # noinspection PyProtectedMember
            in_node_link = urlparse(nodes[edge['in_node']]['url'])._replace(query='format=json')

            edge['out_node'] = out_node_link.geturl()
            edge['in_node'] = in_node_link.geturl()
            edge['protocol'] = edge['protocol'].upper()

        return request

    def retrieve(self, request, *args, **kwargs):
        """
        Возвращает информацию о цепочке с указанным id.

        Пример запроса:

            GET http://127.0.0.1:8000/chain/8/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Пример ответа:

            {
              "url":"http://127.0.0.1:8000/chain/8/?format=json",
              "title":"chain-name",
              "edges": [
                {
                  "url": "http://127.0.0.1:8000/edge/1/?format=json",
                  "in_node": "http://127.0.0.1:8000/node/8/?format=json",
                  "out_node": "http://127.0.0.1:8000/node/9/?format=json",
                  "protocol": "ssh"
                }, {
                  "url": "http://127.0.0.1:8000/edge/2/?format=json",
                  "in_node": "http://127.0.0.1:8000/node/7/?format=json",
                  "out_node": "http://127.0.0.1:8000/node/8/?format=json",
                  "protocol": "ssh"
                }
              ]
            }

        Инициатор отправляет GET запрос в кодировке utf-8. В URL содержится id цепочки - первичный
        ключ объекта в базе данных. В случае успешной обработки запроса вернётся ответ со статусом 200 (OK).
        В теле ответа содержится информация о запрашиваемой цепочке. В объекте содержатся
        следующие поля:

        * "url" - URL объекта (может использоваться для указания взаимосвязи объектов);
        * "title" - название цепочки;
        * "edges" - спсиок объектов соединений;

        Объекты соединений содержат в себе поля:

        * "url" - URL объекта (может использоваться для указания взаимосвязи объектов);
        * "in_node" - URL объекта узла-источник;
        * "out_node" - URL объекта узла-приемник;
        * "protocol" - протокол взаимосвязи;

        В случае, если цепочка с указанным в запросе id не существует, вернётся ответ со статусом 404 (Not Found).
        """
        return super(ChainView, self).retrieve(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        """
        Изменяет соединение между узлами с указанным id.

        Пример запроса:

            PUT http://127.0.0.1:8000/chain/8/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "title":"chain-new-name",
              "edges": [
                {
                  "url": "http://127.0.0.1:8000/edge/1/?format=json",
                  "in_node": "http://127.0.0.1:8000/node/8/?format=json",
                  "out_node": "http://127.0.0.1:8000/node/9/?format=json",
                  "protocol": "ssh"
                }, {
                  "url": "http://127.0.0.1:8000/edge/2/?format=json",
                  "in_node": "http://127.0.0.1:8000/node/7/?format=json",
                  "out_node": "http://127.0.0.1:8000/node/8/?format=json",
                  "protocol": "ssh"
                }
              ]
            }

        Пример ответа:

            {
              "url":"http://127.0.0.1:8000/chain/8/?format=json",
              "title":"chain-new-name",
              "edges": [
                {
                  "url": "http://127.0.0.1:8000/edge/1/?format=json",
                  "in_node": "http://127.0.0.1:8000/node/8/?format=json",
                  "out_node": "http://127.0.0.1:8000/node/9/?format=json",
                  "protocol": "ssh"
                }, {
                  "url": "http://127.0.0.1:8000/edge/2/?format=json",
                  "in_node": "http://127.0.0.1:8000/node/7/?format=json",
                  "out_node": "http://127.0.0.1:8000/node/8/?format=json",
                  "protocol": "ssh"
                }
              ]
            }

        Инициатор отправляет PUT запрос в кодировке utf-8. В URL содержится id цепочки - первичный
        ключ объекта в базе данных. В теле запроса содержится JSON с полями, значение которых необходимо заменить для
        цепочки.

        В случае успешной обработки запроса вернётся ответ со статусом 200 (OK). В теле ответа содержится
        отредактированная информация о цепочке.

        В случае, если цепока с указанным в запросе id не существует, вернётся ответ со статусом 404
        (Not Found).

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.
        """
        update = super(ChainView, self).update(request, *args, **kwargs)
        logger.info(f'Updated chain with pk={kwargs["pk"]}. '
                    f'Subject: {request.user}')
        return update

    def destroy(self, request, *args, **kwargs):
        """
        Удаляет цепочку с указанным id.

        Пример запроса:

            DELETE http://127.0.0.1:8000/chain/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Инициатор отправляет DELETE запрос в кодировке utf-8. В URL содержится id цепочки - первичный
        ключ объекта в базе данных.

        В случае успешной обработки запроса вернётся ответ со статусом 204 (No Content).

        В случае, если цепочка с указанным в запросе id не существует, вернётся ответ со статусом 404 (Not Found).
        """

        chain = Chain.objects.get(pk=kwargs["pk"])
        chain_destroying = kill_processes.delay(
            chain_id=chain.id, is_internal=True, task_identifier=f'kill:chain_processes:{chain.id}'
        )
        response = super(ChainView, self).retrieve(request, *args, **kwargs)
        response.status_code = status.HTTP_204_NO_CONTENT
        chain.status = Chain.StatusChoice.BLOCK
        chain.save(update_fields=['status',])
        if self.is_task_finished(task=chain_destroying):
            for node in chain.sorted_nodes:
                node.delete()
        logger.info(f'Deleted chain with pk={kwargs["pk"]}. Subject: {request.user}')
        return response

    @staticmethod
    def is_task_finished(task) -> True:
        """Wait till task finishes.

        Args:
            task: celery task for checking.

        Returns:
            True after celery task finishing.
        """
        while not task.ready():
            time.sleep(WAIT_TIME_BETWEEN_CHECKING_CELERY_TASK_RESULT)
        return True

    def list(self, request, *args, **kwargs):
        """
        Возвращает список доступных цепочек анонимизации.

        Пример запроса:

            GET http://127.0.0.1:8000/chain/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Пример ответа:

            {
              "count": 1,
              "next": null,
              "previous": null,
              "results": [
                {
                  "url":"http://127.0.0.1:8000/chain/8/?format=json",
                  "title":"chain-name",
                  "edges": [
                    {
                      "url": "http://127.0.0.1:8000/edge/1/?format=json",
                      "in_node": "http://127.0.0.1:8000/node/8/?format=json",
                      "out_node": "http://127.0.0.1:8000/node/9/?format=json",
                      "protocol": "ssh"
                    }, {
                      "url": "http://127.0.0.1:8000/edge/2/?format=json",
                      "in_node": "http://127.0.0.1:8000/node/7/?format=json",
                      "out_node": "http://127.0.0.1:8000/node/8/?format=json",
                      "protocol": "ssh"
                    }
                  ]
                }
              ]
            }

        Инициатор отправляет GET запрос в кодировке UTF-8.

        В случае успеха возвращается ответ со статусом 200 (OK). В теле ответа содержится JSON, содержащий следующие
        поля:
        * "count" - содержит количество элементов;
        * "next" - содержит ссылку для загрузки следующей порции результатов или null;
        * "previous" - содержит ссылку для загрузки предыдущей порции результатов или null;
        * "results" - список объектов цепочек между узлами.

        В случае, если была запрошена отсутствующая страница, возвращается ответ со статусом 404 (Not Found).
        """

        return super(ChainView, self).list(request, *args, **kwargs)


class EdgeView(mixins.RetrieveModelMixin,
               mixins.UpdateModelMixin,
               mixins.DestroyModelMixin,
               mixins.ListModelMixin,
               viewsets.GenericViewSet):
    """
    Позволяет работать со соединениями между узлами цепочек анонимизации.

    Доступно только аутентифицированным пользователям.
    """

    queryset = Edge.objects.all()
    serializer_class = EdgeSerializer
    permission_classes = (IsAuthenticated, IsAdminUser)

    filterset_fields = [
        'in_node', 'out_node', 'protocol', 'chain', 'in_node__server__geo', 'in_node__server',
        'in_node__ssh_proc_port', 'out_node__server__geo', 'out_node__server',
        'out_node__ssh_proc_port'
    ]

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
    def list(self, request, *args, **kwargs):
        """
        Возвращает список доступных соединений между узлами цепочек анонимизации.

        Пример запроса:

            GET http://127.0.0.1:8000/edge?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Пример ответа:

            {
              "count": 1,
              "next": null,
              "previous": null,
              "results": [
                {
                  "url": "http://127.0.0.1:8000/edge/1/?format=json",
                  "in_node": "http://127.0.0.1:8000/node/1/?format=json",
                  "out_node": "http://127.0.0.1:8000/node/9/?format=json",
                  "protocol": "ssh"
                }
              ]
            }

        Инициатор отправляет GET запрос в кодировке UTF-8.

        В случае успеха возвращается ответ со статусом 200 (OK). В теле ответа содержится JSON, содержащий следующие
        поля:
        * "count" - содержит количество элементов;
        * "next" - содержит ссылку для загрузки следующей порции результатов или null;
        * "previous" - содержит ссылку для загрузки предыдущей порции результатов или null;
        * "results" - список объектов соединений между узлами.

        В случае, если была запрошена отсутствующая страница, возвращается ответ со статусом 404 (Not Found).
        """

        return super(EdgeView, self).list(request, *args, **kwargs)

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден"})
    def retrieve(self, request, *args, **kwargs):
        """
        Возвращает информацию о связи между узлами с указанным id.

        Пример запроса:

            GET http://127.0.0.1:8000/edge/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/edge/1/?format=json",
              "in_node": "http://127.0.0.1:8000/node/1/?format=json",
              "out_node": "http://127.0.0.1:8000/node/9/?format=json",
              "protocol": "ssh"
            }

        Инициатор отправляет GET запрос в кодировке utf-8. В URL содержится id соединения между узлами - первичный
        ключ объекта в базе данных. В случае успешной обработки запроса вернётся ответ со статусом 200 (OK).
        В теле ответа содержится информация о запрашиваемом соединении между узлами. В объекте содержатся
        следующие поля:

        * "url" - URL объекта (может использоваться для указания взаимосвязи объектов);
        * "in_node" - URL объекта узла-источник;
        * "out_node" - URL объекта узла-приемник;
        * "protocol" - протокол взаимосвязи;

        В случае, если соединение между узлами с указанным в запросе id не существует,
        вернётся ответ со статусом 404 (Not Found).
        """

        return super(EdgeView, self).retrieve(request, *args, **kwargs)

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def update(self, request, *args, **kwargs):
        """
        Изменяет соединение между узлами с указанным id.

        Пример запроса:

            PUT http://127.0.0.1:8000/edge/4/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "name": "edit_example",
              "resource_url": "https://example.com",
              "resource_type": "SOCIAL"
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/edge/4/?format=json",
              "name": "edit_example",
              "resource_url": "https://example.com",
              "resource_type": "SOCIAL"
            }

        Инициатор отправляет PUT запрос в кодировке utf-8. В URL содержится id соединения между узлами - первичный
        ключ объекта в базе данных. В теле запроса содержится JSON с полями, значение которых необходимо заменить для
        соединения между узлами.

        В случае успешной обработки запроса вернётся ответ со статусом 200 (OK). В теле ответа содержится
        отредактированная информация о соединение между узлами.

        В случае, если соединение между узлами с указанным в запросе id не существует, вернётся ответ со статусом 404
        (Not Found).

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.
        """

        update = super(EdgeView, self).update(request, *args, **kwargs)
        logger.info(f'Updated edge with pk={kwargs["pk"]}. '
                    f'Subject: {request.user}')
        return update

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def partial_update(self, request, *args, **kwargs):
        """
        Изменяет поля соединения между узлами с указанным id.

        Пример запроса:

            PATCH http://127.0.0.1:8000/edge/4/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "out_node": "http://127.0.0.1:8000/node/8/?format=json"
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/edge/4/?format=json",
              "in_node": "http://127.0.0.1:8000/node/2/?format=json",
              "out_node": "http://127.0.0.1:8000/node/8/?format=json",
              "protocol": "ssh"
            }

        Инициатор отправляет PATCH запрос в кодировке utf-8. В URL содержится id соединениz между узлами - первичный
        ключ объекта в базе данных. В теле запроса содержится JSON с полями, значение которых необходимо заменить для
        указанной группы.

        В случае успешной обработки запроса вернётся ответ со статусом 200 (OK). В теле ответа содержится
        отредактированная информация об соединениb между узлами.

        В случае, если соединение между узлами с указанным в запросе id не существует, вернётся ответ со статусом 404
        (Not Found).

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.
        """

        update = super(EdgeView, self).partial_update(request, *args, **kwargs)
        logger.info(f'Updated edge with pk={kwargs["pk"]}. '
                    f'Subject: {request.user}')
        return update

    @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
                                    status.HTTP_404_NOT_FOUND: "Объект не найден"})
    def destroy(self, request, *args, **kwargs):
        """
        Удаляет соединение между узлами с указанным id.

        Пример запроса:

            DELETE http://127.0.0.1:8000/edge/5/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Инициатор отправляет DELETE запрос в кодировке utf-8. В URL содержится id соединения между узлами - первичный
        ключ объекта в базе данных.

        В случае успешной обработки запроса вернётся ответ со статусом 204 (No Content).

        В случае, если соединение между узлами с указанным в запросе id не существует, вернётся ответ со статусом 404
        (Not Found).
        """

        destroy = super(EdgeView, self).destroy(request, *args, **kwargs)
        logger.info(f'Deleted edge with pk={kwargs["pk"]}. Subject: {request.user}')
        return destroy


class NodeView(viewsets.ModelViewSet):
    """
    Позволяет работать с узлами цепочек анонимизации.

    Доступно только аутентифицированным пользователям.
    """
    queryset = Node.objects.all()
    serializer_class = NodeSerializer
    permission_classes = (IsAuthenticated, IsAdminUser)

    filterset_fields = [
        'server__geo', 'server', 'ssh_proc_port', 'server__hosting', 'server__hosting__name',
        'server__hosting__url', 'server__ssh_ip', 'server__ssh_port'
    ]
    search_fields = ['geo', 'ssh_proc_port', 'server__ssh_ip']

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
    def list(self, request, *args, **kwargs):
        """
        Возвращает список доступных узлов.

        Пример запроса:

            GET http://127.0.0.1:8000/node/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Пример ответа:

            {
              "count": 1,
              "next": null,
              "previous": null,
              "results": [
                {
                  "url": "http://127.0.0.1:8000/node/1/?format=json",
                  "certificate": "http://127.0.0.1:8000/node/tmp/cer",
                  "geo": "23.2342343:43.434343",
                  "server": "http://127.0.0.1:8000/server/1/?format=json"
                }
              ]
            }

        Инициатор отправляет GET запрос в кодировке UTF-8.

        В случае успеха возвращается ответ со статусом 200 (OK). В теле ответа содержится JSON, содержащий следующие
        поля:
        * "count" - содержит количество элементов;
        * "next" - содержит ссылку для загрузки следующей порции результатов или null;
        * "previous" - содержит ссылку для загрузки предыдущей порции результатов или null;
        * "results" - список объектов узлов.

        В случае, если была запрошена отсутствующая страница, возвращается ответ со статусом 404 (Not Found).
        """

        return super(NodeView, self).list(request, *args, **kwargs)

    @swagger_auto_schema(responses={status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def create(self, request, *args, **kwargs):
        """
        Добавляет узел.

        Пример запроса:

            POST http://127.0.0.1:8000/node/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "certificate": "http://127.0.0.1:8000/node/tmp/cer",
              "geo": "23.2342343:43.434343",
              "server": "http://127.0.0.1:8000/server/1/?format=json"
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/node/1/?format=json",
              "certificate": "http://127.0.0.1:8000/node/tmp/cer",
              "geo": "23.2342343:43.434343",
              "server": "http://127.0.0.1:8000/server/1/?format=json"
            }

        Инициатор отправляет POST запрос в кодировке UTF-8. В теле запроса должен содержаться JSON
        c обязательными полями:

        * "certificate" - файл-сертификат используемый узлом;
        * "geo" - географическое положение узла;
        * "server" - URL объекта используемого сервера.

        В случае успешного создания узла возвращается ответ со статусом 201 (Created)

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.

        Пример запроса с ошибкой:

            POST http://127.0.0.1:8000/node/2/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "certificate": "http://127.0.0.1:8000/node/tmp/cer",
              "server": "http://127.0.0.1:8000/server/1/?format=json"
            }

        Пример ответа на запрос с ошибкой:

            {
              "geo": [
                "This field may not be blank."
              ]
            }
        """

        create = super(NodeView, self).create(request, *args, **kwargs)
        logger.info(f'Created node {create.data.serializer.instance} '
                    f'with pk={create.data.serializer.instance.pk}. '
                    f'Reason_phrase: {create.reason_phrase}. '
                    f'Subject: {request.user}')
        return create

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден"})
    def retrieve(self, request, *args, **kwargs):
        """
        Возвращает информацию об интернет-ресурсе с указанным id.

        Пример запроса:

            GET http://127.0.0.1:8000/node/2/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/node/2/?format=json",
              "certificate": "http://127.0.0.1:8000/node/tmp/cer",
              "geo": "23.2342343:43.434343",
              "server": "http://127.0.0.1:8000/server/1/?format=json"
            }

        Инициатор отправляет GET запрос в кодировке utf-8. В URL содержится id узла - первичный
        ключ объекта в базе данных. В случае успешной обработки запроса вернётся ответ со статусом 200 (OK).
        В теле ответа содержится информация о запрашиваемом интернет-ресурсе. В объекте содержатся следующие поля:

        * "url" - URL объекта (может использоваться для указания взаимосвязи объектов);
        * "certificate" - файл-сертификат используемый узлом;
        * "geo" - географическое положение узла;
        * "server" - URL объекта используемого сервера.

        В случае, если узел с указанным в запросе id не существует, вернётся ответ со статусом 404
        (Not Found).
        """
        return super(NodeView, self).retrieve(request, *args, **kwargs)

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def update(self, request, *args, **kwargs):
        """
        Изменяет узел с указанным id.

        Пример запроса:

            PUT http://127.0.0.1:8000/node/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "certificate": "http://127.0.0.1:8000/node/tmp/cer",
              "geo": "33.2342343:43.434343",
              "server": "http://127.0.0.1:8000/server/1/?format=json"
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/node/1/?format=json",
              "certificate": "http://127.0.0.1:8000/node/tmp/cer",
              "geo": "33.2342343:43.434343",
              "server": "http://127.0.0.1:8000/server/1/?format=json"
            }

        Инициатор отправляет PUT запрос в кодировке utf-8. В URL содержится id узла - первичный
        ключ объекта в базе данных. В теле запроса содержится JSON с полями, значение которых необходимо заменить для
        указанного узла.

        В случае успешной обработки запроса вернётся ответ со статусом 200 (OK). В теле ответа содержится
        отредактированная информация об узле.

        В случае, если узел с указанным в запросе id не существует, вернётся ответ со статусом 404 (Not Found).

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.

        Пример ответа на запрос с ошибкой:

            {
              "geo": [
                "This field is required."
              ]
            }
        """

        update = super(NodeView, self).update(request, *args, **kwargs)
        logger.info(f'Updated node {update.data.serializer.instance} '
                    f'with pk={update.data.serializer.instance.pk}. '
                    f'Reason_phrase: {update.reason_phrase}. '
                    f'Subject: {request.user}')
        return update

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def partial_update(self, request, *args, **kwargs):
        """
        Изменяет поля узла с указанным id.

        Пример запроса:

            PATCH http://127.0.0.1:8000/node/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "geo": "33.2342343:43.434343",
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/node/1/?format=json",
              "certificate": "http://127.0.0.1:8000/node/tmp/cer",
              "geo": "33.2342343:43.434343",
              "server": "http://127.0.0.1:8000/server/1/?format=json"
            }

        Инициатор отправляет PATCH запрос в кодировке utf-8. В URL содержится id узла - первичный
        ключ объекта в базе данных. В теле запроса содержится JSON с полями, значение которых необходимо заменить для
        указанного узла.

        В случае успешной обработки запроса вернётся ответ со статусом 200 (OK). В теле ответа содержится
        отредактированная информация об узле.

        В случае, если узел с указанным в запросе id не существует, вернётся ответ со статусом 404 (Not Found).

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.
        """

        update = super(NodeView, self).partial_update(request, *args, **kwargs)
        logger.info(f'Updated node {update.data.serializer.instance} '
                    f'with pk={update.data.serializer.instance.pk}. '
                    f'Reason_phrase: {update.reason_phrase}. '
                    f'Subject: {request.user}')
        return update

    @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
                                    status.HTTP_404_NOT_FOUND: "Объект не найден"})
    def destroy(self, request, *args, **kwargs):
        """
        Удаляет узел с указанным id.

        Пример запроса:

            DELETE http://127.0.0.1:8000/node/2/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Инициатор отправляет DELETE запрос в кодировке utf-8. В URL содержится id узла - первичный
        ключ объекта в базе данных.

        В случае успешной обработки запроса вернётся ответ со статусом 204 (No Content).

        В случае, если узел с указанным в запросе id не существует, вернёт ответ со статусом 404 (Not Found).
        """
        destroy = super(NodeView, self).destroy(request, *args, **kwargs)
        logger.info(f'Deleted node with pk={kwargs["pk"]}. Subject: {request.user}')
        return destroy

    def perform_update(self, serializer):
        with transaction.atomic():
            instance = serializer.save()
            self.validate(instance)

    @classmethod
    def validate(cls, node: Node):
        node.validate()


class LargeResultsSetPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 10000


# noinspection DuplicatedCode
class ServerView(viewsets.ModelViewSet):
    """
    Позволяет работать с данными серверов.

    Доступно только аутентифицированным пользователям.
    """
    queryset = Server.objects.all()
    serializer_class = ServerSerializer
    permission_classes = (IsAuthenticated, IsAdminUser)
    pagination_class = LargeResultsSetPagination

    filterset_fields = [
        'server_account', 'server_account__username', 'hosting', 'hosting__name',
        'hosting__url', 'ssh_ip', 'ssh_port'
    ]
    search_fields = ['server_account__username', 'hosting__name', 'hosting__url', 'ssh_ip', 'ssh_port']

    def get_queryset(self):
        servers = Server.objects.all()

        if self.request.query_params.get('available', '').lower() in {'1', 'true', 'y', 'yes'}:
            return servers.filter(id__in=[server.id for server in servers if not server.in_use])

        return servers

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
    def list(self, request, *args, **kwargs):
        """
        Возвращает список доступных узлов.

        Пример запроса:

            GET http://127.0.0.1:8000/server/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Пример ответа:

            {
              "count": 1,
              "next": null,
              "previous": null,
              "results": [
                {
                  "url": "http://127.0.0.1:8000/server/1/",
                  "hosting": "http://127.0.0.1:8000/hosting/1/",
                  "ssh_ip": "192.168.5.123",
                  "server_account": "http://127.0.0.1:8000/server_account/1/"
                }
              ]
            }

        Инициатор отправляет GET запрос в кодировке UTF-8.

        В случае успеха возвращается ответ со статусом 200 (OK). В теле ответа содержится JSON, содержащий следующие
        поля:
        * "count" - содержит количество элементов;
        * "next" - содержит ссылку для загрузки следующей порции результатов или null;
        * "previous" - содержит ссылку для загрузки предыдущей порции результатов или null;
        * "results" - список объектов серверов.

        В случае, если была запрошена отсутствующая страница, возвращается ответ со статусом 404 (Not Found).
        """

        return super(ServerView, self).list(request, *args, **kwargs)

    @swagger_auto_schema(responses={status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def create(self, request, *args, **kwargs):
        """
        Добавляет сервер.

        Пример запроса:

            POST http://127.0.0.1:8000/server/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "hosting": "http://127.0.0.1:8000/hosting/1/",
              "ssh_ip": "192.168.5.123"
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/server/1/",
              "hosting": "http://127.0.0.1:8000/hosting/1/",
              "ssh_ip": "192.168.5.123",
              "server_account": null
            }

        Инициатор отправляет POST запрос в кодировке UTF-8. В теле запроса должен содержаться JSON
        c обязательными полями:

        * "hosting" - URL объекта используемого хостинга;
        * "ssh_ip" - ssh ip.

        В случае успешного создания сервера возвращается ответ со статусом 201 (Created)

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.

        Пример запроса с ошибкой:

            POST http://127.0.0.1:8000/server/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "hosting": "http://127.0.0.1:8000/hosting/1/",
              "server_account": "http://127.0.0.1:8000/server_account/1/"
            }

        Пример ответа на запрос с ошибкой:

            {
              "ssh_ip": [
                "This field may not be blank."
              ]
            }
        """

        create = super(ServerView, self).create(request, *args, **kwargs)
        logger.info(f'Created server {create.data.serializer.instance} '
                    f'with pk={create.data.serializer.instance.pk}. '
                    f'Reason_phrase: {create.reason_phrase}. '
                    f'Subject: {request.user}')
        return create

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден"})
    def retrieve(self, request, *args, **kwargs):
        """
        Возвращает информацию об интернет-ресурсе с указанным id.

        Пример запроса:

            GET http://127.0.0.1:8000/server/2/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/server/1/",
              "hosting": "http://127.0.0.1:8000/hosting/1/",
              "ssh_ip": "192.168.5.123",
              "server_account": "http://127.0.0.1:8000/server_account/1/"
            }

        Инициатор отправляет GET запрос в кодировке utf-8. В URL содержится id объекта сервера - первичный
        ключ объекта в базе данных. В случае успешной обработки запроса вернётся ответ со статусом 200 (OK).
        В теле ответа содержится информация о запрашиваемом интернет-ресурсе. В объекте содержатся следующие поля:

        * "url" - URL объекта (может использоваться для указания взаимосвязи объектов);
        * "hosting" - URL объекта используемого хостинга;
        * "ssh_ip" - ssh ip;
        * "server_account" - URL объекта аккаунта сервера.

        В случае, если объектт сервера с указанным в запросе id не существует, вернётся ответ со статусом 404
        (Not Found).
        """

        return super(ServerView, self).retrieve(request, *args, **kwargs)

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def update(self, request, *args, **kwargs):
        """
        Изменяет объект сервера с указанным id.

        Пример запроса:

            PUT http://127.0.0.1:8000/server/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "hosting": "http://127.0.0.1:8000/hosting/1/",
              "ssh_ip": "192.168.5.223",
              "server_account": "http://127.0.0.1:8000/server_account/1/"  # необязательное поле
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/server/1/?format=json",
              "hosting": "http://127.0.0.1:8000/hosting/1/",
              "ssh_ip": "192.168.5.223",
              "server_account": "http://127.0.0.1:8000/server_account/1/"
            }

        Инициатор отправляет PUT запрос в кодировке utf-8. В URL содержится id объекта сервера - первичный
        ключ объекта в базе данных. В теле запроса содержится JSON с полями, значение которых необходимо заменить для
        указанного объекта сервера.

        В случае успешной обработки запроса вернётся ответ со статусом 200 (OK). В теле ответа содержится
        отредактированная информация об объекте сервера.

        В случае, если объекта сервера с указанным в запросе id не существует, вернётся ответ со статусом
        404 (Not Found).

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.

        Пример ответа на запрос с ошибкой:

            {
              "ssh_ip": [
                "This field is required."
              ]
            }
        """

        update = super(ServerView, self).update(request, *args, **kwargs)
        logger.info(f'Updated server {update.data.serializer.instance} '
                    f'with pk={update.data.serializer.instance.pk}. '
                    f'Reason_phrase: {update.reason_phrase}. '
                    f'Subject: {request.user}')
        return update

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def partial_update(self, request, *args, **kwargs):
        """
        Изменяет поля узла с указанным id.

        Пример запроса:

            PATCH http://127.0.0.1:8000/server/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "ssh_ip": "192.168.5.223"
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/server/1/?format=json",
              "hosting": "http://127.0.0.1:8000/hosting/1/",
              "ssh_ip": "192.168.5.223",
              "server_account": "http://127.0.0.1:8000/server_account/1/"
            }

        Инициатор отправляет PATCH запрос в кодировке utf-8. В URL содержится id объекта сервера - первичный
        ключ объекта в базе данных. В теле запроса содержится JSON с полями, значение которых необходимо заменить для
        указанного объекта сервера.

        В случае успешной обработки запроса вернётся ответ со статусом 200 (OK). В теле ответа содержится
        отредактированная информация об объекте сервера.

        В случае, если объект сервера с указанным в запросе id не существует, вернётся ответ со статусом
        404 (Not Found).

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.
        """

        update = super(ServerView, self).partial_update(request, *args, **kwargs)
        logger.info(f'Updated server {update.data.serializer.instance} '
                    f'with pk={update.data.serializer.instance.pk}. '
                    f'Reason_phrase: {update.reason_phrase}. '
                    f'Subject: {request.user}')
        return update

    @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
                                    status.HTTP_404_NOT_FOUND: "Объект не найден"})
    def destroy(self, request, *args, **kwargs):
        """
        Удаляет объект сервера с указанным id.

        Пример запроса:

            DELETE http://127.0.0.1:8000/server/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Инициатор отправляет DELETE запрос в кодировке utf-8. В URL содержится id объекта сервера - первичный
        ключ объекта в базе данных.

        В случае успешной обработки запроса вернётся ответ со статусом 204 (No Content).

        В случае, если объект сервера с указанным в запросе id не существует, вернёт ответ со статусом 404 (Not Found).
        """

        destroy = super(ServerView, self).destroy(request, *args, **kwargs)
        logger.info(f'Deleted server with pk={kwargs["pk"]}. Subject: {request.user}')
        return destroy


# noinspection DuplicatedCode
class HostingView(viewsets.ModelViewSet):
    """
    Позволяет работать с данными хостингов.

    Доступно только аутентифицированным пользователям.
    """

    queryset = Hosting.objects.all()
    serializer_class = HostingSerializer
    permission_classes = (IsAuthenticated, IsAdminUser)

    filterset_fields = ['name', 'url']
    search_fields = ['name', 'url']

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
    def list(self, request, *args, **kwargs):
        """
        Возвращает список доступных объектов хостингов.

        Пример запроса:

            GET http://127.0.0.1:8000/hosting/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Пример ответа:

            {
              "count": 1,
              "next": null,
              "previous": null,
              "results": [
                {
                  "url": "http://127.0.0.1:8000/hosting/1/?format=json",
                  "url": "http://test.org",
                  "name": "test",
                  "hosting_account": "http://127.0.0.1:8000/hosting_account/1/?format=json"
                }
              ]
            }

        Инициатор отправляет GET запрос в кодировке UTF-8.

        В случае успеха возвращается ответ со статусом 200 (OK). В теле ответа содержится JSON, содержащий следующие
        поля:
        * "count" - содержит количество элементов;
        * "next" - содержит ссылку для загрузки следующей порции результатов или null;
        * "previous" - содержит ссылку для загрузки предыдущей порции результатов или null;
        * "results" - список объектов хостингов.

        В случае, если была запрошена отсутствующая страница, возвращается ответ со статусом 404 (Not Found).
        """

        return super(HostingView, self).list(request, *args, **kwargs)

    @swagger_auto_schema(responses={status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def create(self, request, *args, **kwargs):
        """
        Добавляет объект хостинга.

        Пример запроса:

            POST http://127.0.0.1:8000/hosting/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
                "url": "http://test.org",
                "name": "test"
            }

        Пример ответа:

            {
                "url": "http://127.0.0.1:8000/hosting/1/?format=json",
                "url": "http://test.org",
                "name": "test",
                "hosting_account": null
            }

        Инициатор отправляет POST запрос в кодировке UTF-8. В теле запроса должен содержаться JSON
        c обязательными полями:

        * "url" - ссылка на хостинг;
        * "name" - имя объекта хостинга.

        В случае успешного создания объекта хостинга возвращается ответ со статусом 201 (Created)

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.

        Пример запроса с ошибкой:

            POST http://127.0.0.1:8000/hosting/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "url": "http://test.org",
              "hosting_account": "http://127.0.0.1:8000/hosting_account/1/?format=json"
            }

        Пример ответа на запрос с ошибкой:

            {
              "name": [
                "This field may not be blank."
              ]
            }
        """

        create = super(HostingView, self).create(request, *args, **kwargs)
        logger.info(f'Created hosting {create.data.serializer.instance} '
                    f'with pk={create.data.serializer.instance.pk}. '
                    f'Reason_phrase: {create.reason_phrase}. '
                    f'Subject: {request.user}')
        return create

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден"})
    def retrieve(self, request, *args, **kwargs):
        """
        Возвращает информацию об объекте хостинга с указанным id.

        Пример запроса:

            GET http://127.0.0.1:8000/hosting/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/hosting/1/?format=json",
              "url": "http://test.org",
              "name": "test",
              "hosting_account": "http://127.0.0.1:8000/hosting_account/1/?format=json"
            }

        Инициатор отправляет GET запрос в кодировке utf-8. В URL содержится id объекта хостинга - первичный
        ключ объекта в базе данных. В случае успешной обработки запроса вернётся ответ со статусом 200 (OK).
        В теле ответа содержится информация о запрашиваемом объекте хостинга. В объекте содержатся следующие поля:

        * "url" - URL объекта (может использоваться для указания взаимосвязи объектов);
        * "url" - ссылка на хостинг;
        * "name" - имя объекта хостинга;
        * "hosting_account" - URL объекта используемого аккаунта хостингов.

        В случае, если объект хостинга с указанным в запросе id не существует, вернётся ответ со статусом 404
        (Not Found).
        """
        return super(HostingView, self).retrieve(request, *args, **kwargs)

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def update(self, request, *args, **kwargs):
        """
        Изменяет объект хостинга с указанным id.

        Пример запроса:

            PUT http://127.0.0.1:8000/hosting/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "url": "http://test.org",
              "name": "new-test-name",
              "hosting_account": "http://127.0.0.1:8000/hosting_account/1/?format=json"
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/hosting/1/?format=json",
              "url": "http://test.org",
              "name": "new-test-name",
              "hosting_account": "http://127.0.0.1:8000/hosting_account/1/?format=json"
            }

        Инициатор отправляет PUT запрос в кодировке utf-8. В URL содержится id объекта хостинга - первичный
        ключ объекта в базе данных. В теле запроса содержится JSON с полями, значение которых необходимо заменить для
        указанного объекта хостинга.

        В случае успешной обработки запроса вернётся ответ со статусом 200 (OK). В теле ответа содержится
        отредактированная информация об объекте хостинга.

        В случае, если объект хостинга с указанным в запросе id не существует,
        вернётся ответ со статусом 404 (Not Found).

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.

        Пример ответа на запрос с ошибкой:

            {
              "name": [
                "This field is required."
              ]
            }
        """

        update = super(HostingView, self).update(request, *args, **kwargs)
        logger.info(f'Updated hosting {update.data.serializer.instance} '
                    f'with pk={update.data.serializer.instance.pk}. '
                    f'Reason_phrase: {update.reason_phrase}. '
                    f'Subject: {request.user}')
        return update

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def partial_update(self, request, *args, **kwargs):
        """
        Изменяет поля объекта хостинга с указанным id.

        Пример запроса:

            PATCH http://127.0.0.1:8000/hosting/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
                "name": "new-test-name"
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/hosting/1/?format=json",
              "url": "http://test.org",
              "name": "new-test-name",
              "hosting_account": "http://127.0.0.1:8000/hosting_account/1/?format=json"
            }

        Инициатор отправляет PATCH запрос в кодировке utf-8. В URL содержится id объекта хостинга - первичный
        ключ объекта в базе данных. В теле запроса содержится JSON с полями, значение которых необходимо заменить для
        указанного объекта хостинга.

        В случае успешной обработки запроса вернётся ответ со статусом 200 (OK). В теле ответа содержится
        отредактированная информация об объекте хостинга.

        В случае, если объект хостинга с указанным в запросе id не существует, вернётся ответ со статусом
        404 (Not Found).

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.
        """

        update = super(HostingView, self).partial_update(request, *args, **kwargs)
        logger.info(f'Updated hosting {update.data.serializer.instance} '
                    f'with pk={update.data.serializer.instance.pk}. '
                    f'Reason_phrase: {update.reason_phrase}. '
                    f'Subject: {request.user}')
        return update

    @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
                                    status.HTTP_404_NOT_FOUND: "Объект не найден"})
    def destroy(self, request, *args, **kwargs):
        """
        Удаляет объект хостинга с указанным id.

        Пример запроса:

            DELETE http://127.0.0.1:8000/hosting/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Инициатор отправляет DELETE запрос в кодировке utf-8. В URL содержится id объекта хостинга - первичный
        ключ объекта в базе данных.

        В случае успешной обработки запроса вернётся ответ со статусом 204 (No Content).

        В случае, если объект хостинга с указанным в запросе id не существует, вернёт ответ со статусом 404 (Not Found).
        """
        destroy = super(HostingView, self).destroy(request, *args, **kwargs)
        logger.info(f'Deleted hosting with pk={kwargs["pk"]}. Subject: {request.user}')
        return destroy


# noinspection DuplicatedCode
class ServerAccountView(viewsets.ModelViewSet):
    """
    Позволяет работать с данными аккаунтов серверов.

    Доступно только аутентифицированным администраторам.
    """

    queryset = SrvAccount.objects.all()
    serializer_class = ServerAccountSerializer
    permission_classes = (IsAuthenticated, IsAdminUser)

    filterset_fields = ['username', 'password', 'server', 'server__hosting', 'server__hosting__name',
                        'server__hosting__url', 'server__ssh_ip', 'server__ssh_port']
    search_fields = ['username', 'password', 'server__hosting__name', 'server__hosting__url', 'server__ssh_ip',
                     'server__ssh_port', 'server__ssh_port']

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
    def list(self, request, *args, **kwargs):
        """
        Возвращает список доступных объектов аккаунтов серверов.

        Пример запроса:

            GET http://127.0.0.1:8000/server_account/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Пример ответа:

            {
              "count": 1,
              "next": null,
              "previous": null,
              "results": [
                {
                  "url": "http://127.0.0.1:8000/server_account/1/?format=json",
                  "username": "admin",
                  "password": "qwerty",
                  "server": "http://127.0.0.1:8000/server/2/"
                }
              ]
            }

        Инициатор отправляет GET запрос в кодировке UTF-8.

        В случае успеха возвращается ответ со статусом 200 (OK). В теле ответа содержится JSON, содержащий следующие
        поля:
        * "count" - содержит количество элементов;
        * "next" - содержит ссылку для загрузки следующей порции результатов или null;
        * "previous" - содержит ссылку для загрузки предыдущей порции результатов или null;
        * "results" - список объектов аккаунтов серверов.

        В случае, если была запрошена отсутствующая страница, возвращается ответ со статусом 404 (Not Found).
        """

        return super(ServerAccountView, self).list(request, *args, **kwargs)

    @swagger_auto_schema(responses={status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def create(self, request, *args, **kwargs):
        """
        Добавляет объект аккаунта сервера.

        Пример запроса:

            POST http://127.0.0.1:8000/node/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "username": "admin",
              "password": "qwerty",
              "server": "http://127.0.0.1:8000/server/1/"
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/server_account/1/?format=json",
              "username": "admin",
              "password": "qwerty",
              "server": "http://127.0.0.1:8000/server/1/"
            }

        Инициатор отправляет POST запрос в кодировке UTF-8. В теле запроса должен содержаться JSON
        c обязательными полями:

        * "username" - имя пользователя создаваемого аккаунта;
        * "password" - пароль пользователя создаваемого аккаунта;
        * "server" - URL объекта используемого сервера.

        В случае успешного создания объект аккаунта сервера возвращается ответ со статусом 201 (Created)

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.

        Пример запроса с ошибкой:

            POST http://127.0.0.1:8000/node/2/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "username": "http://127.0.0.1:8000/node/tmp/cer",
              "server": "http://127.0.0.1:8000/server/1/?format=json"
            }

        Пример ответа на запрос с ошибкой:

            {
              "password": [
                "This field may not be blank."
              ]
            }
        """

        create = super(ServerAccountView, self).create(request, *args, **kwargs)
        logger.info(f'Created server account {create.data.serializer.instance} '
                    f'with pk={create.data.serializer.instance.pk}. '
                    f'Reason_phrase: {create.reason_phrase}. '
                    f'Subject: {request.user}')
        return create

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден"})
    def retrieve(self, request, *args, **kwargs):
        """
        Возвращает информацию об объект аккаунта сервера с указанным id.

        Пример запроса:

            GET http://127.0.0.1:8000/server_account/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/server_account/1/?format=json",
              "username": "admin",
              "password": "qwerty",
              "server": "http://127.0.0.1:8000/server/1/"
            }

        Инициатор отправляет GET запрос в кодировке utf-8. В URL содержится id объект аккаунта сервера - первичный
        ключ объекта в базе данных. В случае успешной обработки запроса вернётся ответ со статусом 200 (OK).
        В теле ответа содержится информация о запрашиваемом объекте аккаунта сервера. В объекте содержатся
        следующие поля:

        * "url" - URL объекта (может использоваться для указания взаимосвязи объектов);
        * "username" - имя пользователя создаваемого аккаунта;
        * "password" - пароль пользователя создаваемого аккаунта;
        * "server" - URL объекта используемого сервера.

        В случае, если объект аккаунта сервера с указанным в запросе id не существует, вернётся ответ со статусом 404
        (Not Found).
        """
        return super(ServerAccountView, self).retrieve(request, *args, **kwargs)

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def update(self, request, *args, **kwargs):
        """
        Изменяет объект аккаунта сервера с указанным id.

        Пример запроса:

            PUT http://127.0.0.1:8000/server_account/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "username": "admin",
              "password": "qwertyqwerty",
              "server": "http://127.0.0.1:8000/server/1/"
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/server_account/1/?format=json",
              "username": "admin",
              "password": "qwertyqwerty",
              "server": "http://127.0.0.1:8000/server/1/"
            }

        Инициатор отправляет PUT запрос в кодировке utf-8. В URL содержится id объекта аккаунта сервера - первичный
        ключ объекта в базе данных. В теле запроса содержится JSON с полями, значение которых необходимо заменить для
        указанного объекта аккаунта сервера.

        В случае успешной обработки запроса вернётся ответ со статусом 200 (OK). В теле ответа содержится
        отредактированная информация об объекте аккаунта сервера.

        В случае, если объект аккаунта сервера с указанным в запросе id не существует, вернётся ответ со статусом
        404 (Not Found).

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.

        Пример ответа на запрос с ошибкой:

            {
              "username": [
                "This field is required."
              ]
            }
        """

        update = super(ServerAccountView, self).update(request, *args, **kwargs)
        logger.info(f'Updated server account {update.data.serializer.instance} '
                    f'with pk={update.data.serializer.instance.pk}. '
                    f'Reason_phrase: {update.reason_phrase}. '
                    f'Subject: {request.user}')
        return update

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def partial_update(self, request, *args, **kwargs):
        """
        Изменяет поля объекта аккаунта сервера с указанным id.

        Пример запроса:

            PATCH http://127.0.0.1:8000/server_account/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "password": "qwertyqwerty",
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/server_account/1/?format=json",
              "username": "admin",
              "password": "qwertyqwerty",
              "server": "http://127.0.0.1:8000/server/1/"
            }

        Инициатор отправляет PATCH запрос в кодировке utf-8. В URL содержится id объект аккаунта сервера - первичный
        ключ объекта в базе данных. В теле запроса содержится JSON с полями, значение которых необходимо заменить для
        указанного объект аккаунта сервера.

        В случае успешной обработки запроса вернётся ответ со статусом 200 (OK). В теле ответа содержится
        отредактированная информация об объекте аккаунта сервера.

        В случае, если объект аккаунта сервера с указанным в запросе id не существует, вернётся ответ со статусом
        404 (Not Found).

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.
        """

        update = super(ServerAccountView, self).partial_update(request, *args, **kwargs)
        logger.info(f'Updated server account {update.data.serializer.instance} '
                    f'with pk={update.data.serializer.instance.pk}. '
                    f'Reason_phrase: {update.reason_phrase}. '
                    f'Subject: {request.user}')
        return update

    @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
                                    status.HTTP_404_NOT_FOUND: "Объект не найден"})
    def destroy(self, request, *args, **kwargs):
        """
        Удаляет объект аккаунта сервера с указанным id.

        Пример запроса:

            DELETE http://127.0.0.1:8000/server_account/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Инициатор отправляет DELETE запрос в кодировке utf-8. В URL содержится id узла - первичный
        ключ объекта в базе данных.

        В случае успешной обработки запроса вернётся ответ со статусом 204 (No Content).

        В случае, если объект аккаунта сервера с указанным в запросе id не существует, вернёт ответ со статусом
        404 (Not Found).
        """

        destroy = super(ServerAccountView, self).destroy(request, *args, **kwargs)
        logger.info(f'Deleted server account with pk={kwargs["pk"]}. Subject: {request.user}')
        return destroy


# noinspection DuplicatedCode
class HostingAccountView(viewsets.ModelViewSet):
    """
    Позволяет работать с данными аккаунтов хостингов.

    Доступно только аутентифицированным администраторам.
    """

    queryset = HostingAccount.objects.all()
    serializer_class = HostingAccountSerializer
    permission_classes = (IsAuthenticated, IsAdminUser)

    filterset_fields = ['username', 'password', 'hosting', 'hosting__name', 'hosting__url']
    search_fields = ['username', 'password', 'hosting__name', 'hosting__url']

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
    def list(self, request, *args, **kwargs):
        """
        Возвращает список доступных объектов аккаунтов хостингов.

        Пример запроса:

            GET http://127.0.0.1:8000/hosting_account/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Пример ответа:

            {
              "count": 1,
              "next": null,
              "previous": null,
              "results": [
                {
                  "url": "http://127.0.0.1:8000/hosting_account/1/?format=json",
                  "username": "test",
                  "password": "test",
                  "hosting": "http://127.0.0.1:8000/hosting/1/?format=json"
                }
              ]
            }

        Инициатор отправляет GET запрос в кодировке UTF-8.

        В случае успеха возвращается ответ со статусом 200 (OK). В теле ответа содержится JSON, содержащий следующие
        поля:
        * "count" - содержит количество элементов;
        * "next" - содержит ссылку для загрузки следующей порции результатов или null;
        * "previous" - содержит ссылку для загрузки предыдущей порции результатов или null;
        * "results" - список объектов узлов.

        В случае, если была запрошена отсутствующая страница, возвращается ответ со статусом 404 (Not Found).
        """

        return super(HostingAccountView, self).list(request, *args, **kwargs)

    @swagger_auto_schema(responses={status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def create(self, request, *args, **kwargs):
        """
        Добавляет узел.

        Пример запроса:

            POST http://127.0.0.1:8000/hosting_account/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "username": "test",
              "password": "test",
              "hosting": "http://127.0.0.1:8000/hosting/1/?format=json"
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/hosting_account/1/?format=json",
              "username": "test",
              "password": "test",
              "hosting": "http://127.0.0.1:8000/hosting/1/?format=json"
            }

        Инициатор отправляет POST запрос в кодировке UTF-8. В теле запроса должен содержаться JSON
        c обязательными полями:

        * "username" - имя пользователя создаваемого аккаунта;
        * "password" - пароль пользователя создаваемого аккаунта;
        * "hosting" - URL объекта используемого хостинга.

        В случае успешного создания узла возвращается ответ со статусом 201 (Created)

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.
        """

        create = super(HostingAccountView, self).create(request, *args, **kwargs)
        logger.info(f'Created hosting account {create.data.serializer.instance} '
                    f'with pk={create.data.serializer.instance.pk}. '
                    f'Reason_phrase: {create.reason_phrase}. '
                    f'Subject: {request.user}')
        return create

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден"})
    def retrieve(self, request, *args, **kwargs):
        """
        Возвращает информацию об объекте аккаунта хостинга с указанным id.

        Пример запроса:

            GET http://127.0.0.1:8000/hosting_account/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/hosting_account/1/?format=json",
              "username": "test",
              "password": "test",
              "hosting": "http://127.0.0.1:8000/hosting/1/?format=json"
            }

        Инициатор отправляет GET запрос в кодировке utf-8. В URL содержится id объекта аккаунта хостинга - первичный
        ключ объекта в базе данных. В случае успешной обработки запроса вернётся ответ со статусом 200 (OK).
        В теле ответа содержится информация о запрашиваемом объекте аккаунта хостинга. В объекте содержатся
        следующие поля:

        * "url" - URL объекта (может использоваться для указания взаимосвязи объектов);
        * "username" - имя пользователя создаваемого аккаунта;
        * "password" - пароль пользователя создаваемого аккаунта;
        * "hosting" - URL объекта используемого хостинга.

        В случае, если объект аккаунта хостинга с указанным в запросе id не существует, вернётся ответ со статусом
        404 (Not Found).
        """

        return super(HostingAccountView, self).retrieve(request, *args, **kwargs)

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def update(self, request, *args, **kwargs):
        """
        Изменяет объект аккаунта хостинга с указанным id.

        Пример запроса:

            PUT http://127.0.0.1:8000/hosting_account/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "username": "test",
              "password": "test2",
              "hosting": "http://127.0.0.1:8000/hosting/1/?format=json"
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/hosting_account/1/?format=json",
              "username": "test",
              "password": "test2",
              "hosting": "http://127.0.0.1:8000/hosting/1/?format=json"
            }

        Инициатор отправляет PUT запрос в кодировке utf-8. В URL содержится id объекта аккаунта хостинга - первичный
        ключ объекта в базе данных. В теле запроса содержится JSON с полями, значение которых необходимо заменить для
        указанного объекта аккаунта хостинга.

        В случае успешной обработки запроса вернётся ответ со статусом 200 (OK). В теле ответа содержится
        отредактированная информация об объекте аккаунта хостинга.

        В случае, если объект аккаунта хостинга с указанным в запросе id не существует, вернётся ответ со статусом
        404 (Not Found).

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.
        """

        update = super(HostingAccountView, self).update(request, *args, **kwargs)
        logger.info(f'Updated hosting account {update.data.serializer.instance} '
                    f'with pk={update.data.serializer.instance.pk}. '
                    f'Reason_phrase: {update.reason_phrase}. '
                    f'Subject: {request.user}')
        return update

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def partial_update(self, request, *args, **kwargs):
        """
        Изменяет поля объекта аккаунта хостинга с указанным id.

        Пример запроса:

            PATCH http://127.0.0.1:8000/hosting_account/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2
            Content-Type: application/json

            {
              "password": "test2",
            }

        Пример ответа:

            {
              "url": "http://127.0.0.1:8000/hosting_account/1/?format=json",
              "username": "test",
              "password": "test2",
              "hosting": "http://127.0.0.1:8000/hosting/1/?format=json"
            }

        Инициатор отправляет PATCH запрос в кодировке utf-8. В URL содержится id объекта аккаунта хостинга - первичный
        ключ объекта в базе данных. В теле запроса содержится JSON с полями, значение которых необходимо заменить для
        указанного объекта аккаунта хостинга.

        В случае успешной обработки запроса вернётся ответ со статусом 200 (OK). В теле ответа содержится
        отредактированная информация об объекте аккаунта хостинга.

        В случае, если объект аккаунта хостинга с указанным в запросе id не существует,
        вернётся ответ со статусом 404 (Not Found).

        В случае, если запрос сформирован неверно, возвращается ответ со статусом 400 (Bad Request). В теле содержится
        JSON с указанием поля, которое было заполнено неверно.
        """

        update = super(HostingAccountView, self).partial_update(request, *args, **kwargs)
        logger.info(f'Updated hosting account {update.data.serializer.instance} '
                    f'with pk={update.data.serializer.instance.pk}. '
                    f'Reason_phrase: {update.reason_phrase}. '
                    f'Subject: {request.user}')
        return update

    @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
                                    status.HTTP_404_NOT_FOUND: "Объект не найден"})
    def destroy(self, request, *args, **kwargs):
        """
        Удаляет объект аккаунта хостинга с указанным id.

        Пример запроса:

            DELETE http://127.0.0.1:8000/hosting_account/1/?format=json
            Authorization: Token 57fe1b7878d6e50347713f89581c0ca07f250de2

        Инициатор отправляет DELETE запрос в кодировке utf-8. В URL содержится id объекта аккаунта хостинга - первичный
        ключ объекта в базе данных.

        В случае успешной обработки запроса вернётся ответ со статусом 204 (No Content).

        В случае, если объект аккаунта хостинга с указанным в запросе id не существует, вернёт ответ со статусом
        404 (Not Found).
        """
        destroy = super(HostingAccountView, self).destroy(request, *args, **kwargs)
        logger.info(f'Deleted hosting account with pk={kwargs["pk"]}. Subject: {request.user}')
        return destroy


class AppImageView(viewsets.ModelViewSet):
    """
    Позволяет работать с данными аккаунтов хостингов.

    Доступно только аутентифицированным администраторам.
    """

    queryset = AppImage.objects.all()
    serializer_class = AppImageSerializer
    permission_classes = (IsAuthenticated, IsAdminUser)

    filterset_fields = ['title', 'name']
    search_fields = ['title', 'name']

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
    def create(self, request, *args, **kwargs):
        create = super(AppImageView, self).create(request, *args, **kwargs)
        logger.info(f'Created app image {create.data.serializer.instance} '
                    f'with pk={create.data.serializer.instance.pk}. '
                    f'Reason_phrase: {create.reason_phrase}. '
                    f'Subject: {request.user}')
        return create

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def update(self, request, *args, **kwargs):
        update = super(AppImageView, self).update(request, *args, **kwargs)
        logger.info(f'Updated app image {update.data.serializer.instance} '
                    f'with pk={update.data.serializer.instance.pk}. '
                    f'Reason_phrase: {update.reason_phrase}. '
                    f'Subject: {request.user}')
        return update

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def partial_update(self, request, *args, **kwargs):
        update = super(AppImageView, self).partial_update(request, *args, **kwargs)
        return update

    @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
                                    status.HTTP_404_NOT_FOUND: "Объект не найден"})
    def destroy(self, request, *args, **kwargs):
        destroy = super(AppImageView, self).destroy(request, *args, **kwargs)
        logger.info(f'Deleted app image with pk={kwargs["pk"]}. Subject: {request.user}')
        return destroy


class ChainIpAssociate(APIView):
    """
    Возвращает ноды и их ip адреса, ассоциированные с цепочкой chain_pk
    """
    permission_classes = (IsAuthenticated, IsAdminUser)

    def get(self, request, chain_pk, format=None):
        logger.info(f'Getting chain nodes and api for chain_pk={chain_pk}. Subject: {request.user}')
        try:
            chain = Chain.objects.get(pk=chain_pk)
            response_data = chain.get_nodes_ip_list()
            return Response(response_data)
        except ObjectDoesNotExist:
            logger.warning(f'Chain with chain_pk={chain_pk} does not exist. Subject: {request.user}')
            return Response([], status=status.HTTP_404_NOT_FOUND)
        except Exception:
            logger.error(f'Unexpected error. Subject: {request.user}. chain_pk={chain_pk}')
            return Response([], status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ProxyView(viewsets.ModelViewSet):
    queryset = Proxy.objects.all()
    serializer_class = ProxySerializer
    permission_classes = (IsAuthenticated, IsAdminUser)

    filterset_fields = ['protocol', 'username', 'password', 'ip', 'port', 'location', 'state', 'secure_flag']
    search_fields = ['username', 'ip', 'port', ]

    @action(detail=False, methods=['get'], url_path='statistics')
    def statistics(self, request, *args, **kwargs):
        return Response(Proxy.objects.get_statistics())

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: 'Страница не найдена'})
    def create(self, request, *args, **kwargs):
        create = super(ProxyView, self).create(request, *args, **kwargs)
        logger.info(f'Created proxy {create.data.serializer.instance} '
                    f'with pk={create.data.serializer.instance.pk}. '
                    f'Reason_phrase: {create.reason_phrase}. '
                    f'Subject: {request.user}')
        return create

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def update(self, request, *args, **kwargs):
        update = super(ProxyView, self).update(request, *args, **kwargs)
        logger.info(f'Updated proxy {update.data.serializer.instance} '
                    f'with pk={update.data.serializer.instance.pk}. '
                    f'Reason_phrase: {update.reason_phrase}. '
                    f'Subject: {request.user}')
        return update

    @swagger_auto_schema(responses={status.HTTP_404_NOT_FOUND: "Объект не найден",
                                    status.HTTP_400_BAD_REQUEST: 'Ошибка в запросе'})
    def partial_update(self, request, *args, **kwargs):
        update = super(ProxyView, self).partial_update(request, *args, **kwargs)
        return update

    @swagger_auto_schema(responses={status.HTTP_204_NO_CONTENT: "Объект успешно удалён",
                                    status.HTTP_404_NOT_FOUND: "Объект не найден"})
    def destroy(self, request, *args, **kwargs):
        destroy = super(ProxyView, self).destroy(request, *args, **kwargs)
        logger.info(f'Deleted proxy with pk={kwargs["pk"]}. Subject: {request.user}')
        return destroy


@csrf_protect
def import_proxies(request: HttpRequest):
    if not request.user or not request.user.is_staff:
        return HttpResponseForbidden('You are not staff.')

    form = ImportProxiesForm()

    ctx = {
        'form': form,
        'cl': {'opts': Proxy._meta},
        'app_label': 'anon_app'
    }

    if request.method == 'GET':
        return render(request, 'admin/import_bots.html', ctx)

    elif request.method != 'POST':
        return

    form = ImportProxiesForm(request.POST, request.FILES)
    check_proxies = request.POST.get('check_proxies', False)
    anon_chains = request.POST.get('chain', False)

    context = {
        'form': form,
        'cl': {'opts': Proxy._meta},
        'app_label': 'anon_app'
    }

    if not form.is_valid():
        return HttpResponseRedirect('../')

    elif check_proxies and not anon_chains:
        messages.error(request, 'Необходимо выбрать цепочку анонимизации')
        return render(request, 'admin/import_bots.html', context)

    elif not check_proxies and anon_chains:
        messages.error(request, 'Необходимо выбрать "Проверить прокси сервера"')
        return render(request, 'admin/import_bots.html', context)

    try:
        form.save()
        return HttpResponseRedirect('../')
    except ValueError:
        form.add_error('file', error=ValidationError(f'Проверьте корректность формата файла'))
        logger.warning(f'Проверьте корректность формата файла', exc_info=True)
        return render(request, 'admin/import_bots.html', context)
    except Exception as e:
        form.add_error('file', error=ValidationError(f'Произошла ошибка при импорте файла: {e}'))
        logger.warning(f'Произошла ошибка при импорте файла: {e}', exc_info=True)
        return render(request, 'admin/import_bots.html', context)