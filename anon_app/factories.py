from factory import DjangoModelFactory, Faker, SubFactory, django as django_factory, fuzzy

from anon_app.models import Hosting, HostingAccount, Server, SrvAccount, AppImage, Proxy


class HostingModelFactory(DjangoModelFactory):
 class Meta:
  model = Hosting

 name = Faker('domain_word')
 url = Faker('url')


class HostingAccountModelFactory(DjangoModelFactory):
 class Meta:
  model = HostingAccount

 username = Faker('user_name')
 password = Faker('user_name')

 hosting = SubFactory(HostingModelFactory)


class ServerModelFactory(DjangoModelFactory):
 class Meta:
  model = Server

 hosting = SubFactory(HostingModelFactory)
 ssh_ip = Faker('ipv4')


class ServerAccountModelFactory(DjangoModelFactory):
 class Meta:
  model = SrvAccount

 username = Faker('user_name')
 password = Faker('user_name')

 server = SubFactory(ServerModelFactory)


class AppImageModelFactory(DjangoModelFactory):
 class Meta:
  model = AppImage

 title = Faker('user_name')
 name = 'sos_web-app'
 image = django_factory.FileField(filename='image.zip')
 env = django_factory.FileField(filename='env.env')
 docker_compose = django_factory.FileField(filename='docker-compose.yml')
 browser_profiles = django_factory.FileField(filename='browser_profiles.zip')


class ProxyModelFactory(DjangoModelFactory):
 class Meta:
  model = Proxy

 protocol = fuzzy.FuzzyChoice(
  [
   Proxy.ProtocolChoice.HTTP.value,
   Proxy.ProtocolChoice.HTTPS.value,
   Proxy.ProtocolChoice.Socks5.value
  ])

 username = Faker('user_name')
 password = Faker('user_name')
 ip = Faker('ipv4')
 port = Faker('port_number', is_user=True)
 location = Faker('city')