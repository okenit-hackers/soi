from rest_framework import serializers

from anon_app.models import HostingAccount, Hosting, SrvAccount, Server, Node, Edge, Chain, AppImage, Proxy


class HostingAccountSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = HostingAccount
  fields = '__all__'


class HostingSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = Hosting
  fields = ('pk', 'url', 'name', 'url', 'hosting_account', 'server_set')
  read_only_fields = ('hosting_account',)
  extra_kwargs = {'server_set': {'required': False}}


class ServerAccountSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = SrvAccount
  fields = '__all__'


class ServerSerializer(serializers.HyperlinkedModelSerializer):
 used_in = serializers.SerializerMethodField()
 in_use = serializers.SerializerMethodField()
 role_in_chain = serializers.SerializerMethodField()

 def get_role_in_chain(self, server: Server):
  return server.get_type_display()

 # noinspection PyMethodMayBeStatic
 def get_used_in(self, obj: Server):
  return obj.used_in

 # noinspection PyMethodMayBeStatic
 def get_in_use(self, obj: Server):
  return obj.in_use

 class Meta:
  model = Server
  fields = (
   'pk', 'is_powerful', 'url', 'hosting', 'ssh_ip', 'ssh_port',
   'server_account', 'node', 'used_in', 'in_use', 'geo',
   'anonymization_chain', 'role_in_chain',
  )
  read_only_fields = ('server_account', 'node', 'used_in', 'in_use')
  extra_kwargs = {'node': {'required': False}}


class NodeSerializer(serializers.HyperlinkedModelSerializer):
 used_in = serializers.SerializerMethodField()
 geo = serializers.StringRelatedField(source='server.geo')

 # noinspection PyMethodMayBeStatic
 def get_used_in(self, obj: Node):
  place = obj.used_in
  return place.label if place is not None else None

 class Meta:
  model = Node
  fields = (
   'pk', 'url', 'type', 'is_powerful', 'id_rsa', 'id_rsa_pub', 'server', 'ssh_proc_port',
   'forwarded_zabbix_port', 'used_in', 'in_use', 'geo'
  )
  read_only_fields = ('used_in', 'in_use')


class EdgeSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = Edge
  fields = ('pk', 'url', 'in_node', 'out_node', 'protocol', 'ping', 'upload_speed', 'download_speed')
  read_only_fields = ['ping', 'upload_speed', 'download_speed']


class ChainSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = Chain
  fields = [
   'pk', 'need_pull_accounts', 'for_internet_access', 'has_proxies_chain', 'concurrency', 'url',
   'title', 'task_queue_name', 'edges', 'app_image', 'status', 'proxies_in_chain',
   'openssh_container_id_rsa', 'openssh_container_id_rsa_pub', 'openssh_container_external_port',
   'openssh_container_internal_port', 'proxy_set', 'ping', 'upload_speed', 'download_speed',
   'ports_info', 'last_update_info_dt', 'last_checking_celery_task_id',
   'available_proxies_count', 'all_proxies_count',
  ]
  extra_kwargs = {
   'openssh_container_id_rsa': {'required': False},
   'openssh_container_id_rsa_pub': {'required': False},
   'proxy_set': {'required': False}
  }
  read_only_fields = [
   'status', 'ping', 'upload_speed', 'download_speed', 'ports_info',
   'last_update_info_dt', 'last_checking_celery_task_id'
  ]

 available_proxies_count = serializers.SerializerMethodField()
 all_proxies_count = serializers.SerializerMethodField()

 @staticmethod
 def get_available_proxies_count(chain: Chain):
  return chain.get_alive_proxies_query_with_conditions().count()

 @staticmethod
 def get_all_proxies_count(chain: Chain):
  return chain.proxy_set.count()

 edges = EdgeSerializer(many=True)

 def create(self, validated_data):
  edges_data = validated_data.pop('edges')
  proxy_data = validated_data.pop('proxy_set', [])
  chain = Chain.objects.create(**validated_data)
  for edge_data in edges_data:
   Edge.objects.create(chain=chain, **edge_data)
  for proxy in proxy_data:
   proxy.chain = chain
   proxy.save(update_fields=['chain'])

  return chain

 def update(self, instance, validated_data):
  if 'edges' not in validated_data and 'proxy_set' not in validated_data:
   return super(ChainSerializer, self).update(instance, validated_data)

  edges = validated_data.pop('edges')
  proxy_data = validated_data.pop('proxy_set', [])

  for key, value in validated_data.items():
   setattr(instance, key, value)

  to_create = []
  to_delete = {e for e in instance.edges.all()}
  for edge in edges:
   qs = Edge.objects.filter(**edge, chain=instance)
   if qs.exists():
    to_delete -= {qs.last()}
   else:
    to_create.append(edge)

  for edge in to_create:
   Edge.objects.create(**edge, chain=instance)

  for edge in to_delete:
   edge.delete()

  to_create = []
  to_delete = {e for e in instance.proxy_set.all()}
  for proxy in proxy_data:
   qs = Proxy.objects.filter(**proxy, chain=instance)
   if qs.exists():
    to_delete -= {qs.last()}
   else:
    to_create.append(proxy)

  for proxy in to_create:
   Proxy.objects.create(**proxy, chain=instance)

  for proxy in to_delete:
   proxy.delete()

  instance.save()
  return instance


class AppImageSerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = AppImage
  fields = ['pk', 'url', 'title', 'name', 'image', 'env', 'docker_compose', 'browser_profiles', 'chain_set']
  extra_kwargs = {'chain_set': {'required': False}}


class ProxySerializer(serializers.HyperlinkedModelSerializer):
 class Meta:
  model = Proxy
  fields = ('pk', 'url', 'protocol', 'username', 'password', 'ip', 'port', 'location', 'chain', 'state', 'services')


class ChainsRebuildSerializer(serializers.Serializer):
 with_image = serializers.BooleanField(default=False)


class ChainRebuildSerializer(ChainsRebuildSerializer):
 id = serializers.IntegerField(required=True)