import os

from django.conf import settings
from appconf import AppConf


class AnonAppConf(AppConf):
 MIN_CHAIN_SIZE = int(os.environ.get('ANON_APP_MIN_CHAIN_SIZE')) \
  if int(os.environ.get('ANON_APP_MIN_CHAIN_SIZE', '0')) > 0 else 3

 SSH_KEYS_TYPE = os.environ.get('ANON_APP_SSH_KEYS_TYPE').lower().casefold() \
  if os.environ.get('ANON_APP_SSH_KEYS_TYPE', 'null').lower().casefold() in (
   'dsa', 'ecdsa', 'ed25519', 'rsa'
 ) else 'ecdsa'

 SSH_KEYS_BITS = int(os.environ.get('ANON_APP_SSH_KEYS_BITS')) \
  if int(os.environ.get('ANON_APP_SSH_KEYS_BITS', '0')) >= 128 else 521

 TEST_USER_NAME = 'SOIANONTEST'
 TEST_SUPERUSER_NAME = f'{TEST_USER_NAME}_SU'
 TEST_USER_PASSWORD = TEST_SUPERUSER_PASSWORD = 'qwerty'
 HOST = '127.0.0.1:8000'

 AVAGEN_HOST = os.environ.get('ANON_APP_AVAGEN_HOST', 'localhost')
 AVAGEN_PORT = os.environ.get('ANON_APP_AVAGEN_PORT', 443)
 EXTERNAL_AVAGEN_HOST = os.environ.get('ANON_APP_EXTERNAL_AVAGEN_HOST', 'openssh')
 EXTERNAL_AVAGEN_PORT = os.environ.get('ANON_APP_EXTERNAL_AVAGEN_PORT', 1488)

 OPENVPN_SRV_DIR = os.environ.get(
  'ANON_APP_OPENVPN_SRV_DIR',
  '/etc/openvpn'
 ) # anon_app/ansible-playbooks/openvpn/vars.yml:openvpn_ovpn_dir
 OPENVPN_FETCH_CONFIG_DIR = os.environ.get(
  'ANON_APP_OPENVPN_FETCH_CONFIG_DIR',
  'creds'
 ) # anon_app/ansible-playbooks/openvpn/vars.yml:openvpn_fetch_config_dir
 OPENVPN_NETWORK2SHARE = os.environ.get('ANON_APP_OPENVPN_NETWORK2SHARE') # 14.8.8.0/24
 SWAP_FILE_SIZE_MB = os.environ.get('ANON_APP_SWAP_FILE_SIZE_MB', '1024') # 1024

 # хост и порт куда пробросится zabbix
 EXTERNAL_ZABBIX_HOST = os.environ.get('ANON_APP_EXTERNAL_ZABBIX_HOST', 'localhost')
 EXTERNAL_ZABBIX_PORT = int(os.environ.get('ANON_APP_EXTERNAL_ZABBIX_PORT', '10051'))
 ZABBIX_HOST = os.environ.get('ANON_APP_ZABBIX_HOST', 'zabbix-stub-server')
 ZABBIX_PORT = int(os.environ.get('ANON_APP_ZABBIX_PORT', '10051'))

 ZABBIX_SERVER_URL = os.environ.get('ANON_APP_ZABBIX_SERVER_URL', 'https://web-server/zabbix/')
 ZABBIX_SERVER_USER = os.environ.get('ANON_APP_ZABBIX_SERVER_USER', 'Admin')
 ZABBIX_SERVER_PASSWORD = os.environ.get('ANON_APP_ZABBIX_SERVER_PASSWORD', 'zabbix')

 ZABBIX_SERVER_CHAIN_GROUP = os.environ.get('ANON_APP_ZABBIX_SERVER_CHAIN_GROUP', 'soi chains')

 EXTERNAL_RABBITMQ_HOST = os.environ.get('ANON_APP_EXTERNAL_RABBITMQ_HOST', 'openssh')
 EXTERNAL_RABBITMQ_PORT = int(os.environ.get('ANON_APP_EXTERNAL_RABBITMQ_PORT', '5672'))

 EXTERNAL_REDIS_HOST = os.environ.get('ANON_APP_EXTERNAL_RABBITMQ_HOST', 'openssh')
 EXTERNAL_REDIS_PORT = int(os.environ.get('ANON_APP_EXTERNAL_RABBITMQ_PORT', '6379'))

 # EXTERNAL_ZABBIX_AGENT_HOST = os.environ.get('ANON_APP_EXTERNAL_ZABBIX_AGENT_HOST', 'localhost')
 # EXTERNAL_ZABBIX_AGENT_PORT = int(os.environ.get('ANON_APP_EXTERNAL_ZABBIX_AGENT_PORT', '10050'))
 # ZABBIX_AGENT_HOST = os.environ.get('ANON_APP_ZABBIX_AGENT_HOST', 'zabbix-stub-server')
 # ZABBIX_AGENT_PORT = int(os.environ.get('ANON_APP_ZABBIX_AGENT_PORT', '10050'))
