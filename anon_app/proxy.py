import asyncio
import logging
from asyncio import AbstractEventLoop
from math import ceil, log, sqrt
from typing import Dict, Optional

from aiohttp import ClientSession
from aiohttp.client import ClientTimeout
from aiohttp_proxy import ProxyConnector
from django.utils import timezone

from anon_app.models import Proxy
from anon_app.proxy_locations import proxy_locations, unknown_location

logger = logging.getLogger(__name__)

ATTEMPTS_TO_CHECK_STATE_COUNT = 3
ATTEMPTS_TO_CHECK_LOCATION_COUNT = 2

REQUEST_TIMEOUT = 10
WORKERS_LIMIT = 100


class ProxyChecker:
 proxies: list
 workers: list
 queue: asyncio.Queue
 __event_loop: Optional[AbstractEventLoop] = None

 def __init__(self, proxies: list):
  self.proxies = proxies
  self.proxies_count = len(proxies)
  self.proxies_alive = 0
  self.proxies_died = 0

 @property
 def _workers_count(self):
  return min(ceil(sqrt(len(self.proxies)) * log(len(self.proxies))) + 1, WORKERS_LIMIT)

 @property
 def _event_loop(self):
  if not self.__event_loop:
   self.__event_loop = asyncio.new_event_loop()
   asyncio.set_event_loop(self.__event_loop)
  return self.__event_loop

 @staticmethod
 def proxy_to_url(proxy_fields: Dict) -> str:
  if proxy_fields['username'] and proxy_fields['password']:
   return f'{proxy_fields["protocol"]}://{proxy_fields["username"]}:{proxy_fields["password"]}' \
      f'@{proxy_fields["ip"]}:{proxy_fields["port"]}'
  return f'{proxy_fields["protocol"]}://{proxy_fields["ip"]}:{proxy_fields["port"]}'

 def check_state(self, url: str) -> list:
  self._event_loop.run_until_complete(self._run_tasks(worker=self._check_state, url=url))
  logger.info(
   f'ALL_Proxy - {self.proxies_count}, ALIVE_Proxy - {self.proxies_alive}, DIED_Proxy - {self.proxies_died} '
  )
  return self.proxies

 def check_location(self, url: str) -> list:
  self._event_loop.run_until_complete(self._run_tasks(worker=self._check_location, url=url))
  return self.proxies

 async def _run_tasks(self, worker, *args, **kwargs) -> None:
  self.queue = asyncio.Queue()

  for proxy in self.proxies:
   if 'check' not in proxy:
    proxy['check'] = {}
   proxy['check']['tries_count'] = 0
   self.queue.put_nowait(proxy)

  self.workers = [asyncio.create_task(worker(*args, **kwargs)) for _ in range(self._workers_count)]
  await self.queue.join()

  for worker in self.workers:
   worker.cancel()
  await asyncio.gather(*self.workers, return_exceptions=True)

 async def _check_state(self, url: str) -> None:
  while True:
   proxy = await self.queue.get()
   proxy['check']['tries_count'] += 1

   proxy_str = self.proxy_to_url(proxy['fields'])
   connector = ProxyConnector.from_url(proxy_str, ssl=False)
   timeout = ClientTimeout(total=REQUEST_TIMEOUT)
   session = ClientSession(connector=connector, timeout=timeout, raise_for_status=True)

   try:
    await session.get(url, timeout=REQUEST_TIMEOUT)
   except Exception as exception:
    if proxy['check']['tries_count'] < ATTEMPTS_TO_CHECK_STATE_COUNT:
     self.queue.put_nowait(proxy)
     do = 'Retry.'
    else:
     proxy['fields']['state'] = Proxy.StateChoice.DIED
     do = 'Stop checking.'
     self.proxies_died += 1

    logger.info(' '.join((
     f'Error to check proxy availability for {proxy_str}. {do}',
     f'See exception message: {exception}'
    )))
   else:
    proxy['fields']['state'] = Proxy.StateChoice.ALIVE
    proxy['fields']['last_successful_check_dt'] = timezone.now()
    self.proxies_alive += 1
   finally:
    proxy['fields']['last_check_dt'] = timezone.now()
    proxy['check']['is_dead'] = proxy['fields']['state'] == Proxy.StateChoice.DIED
    await session.close()
    self.queue.task_done()

 async def _check_location(self, url: str) -> None:
  while True:
   proxy = await self.queue.get()
   proxy['check']['tries_count'] += 1

   proxy_str = self.proxy_to_url(proxy['fields'])
   connector = ProxyConnector.from_url(proxy_str, ssl=False)
   timeout = ClientTimeout(total=REQUEST_TIMEOUT)
   session = ClientSession(connector=connector, timeout=timeout, raise_for_status=True)
   try:
    response = await session.get(url)
   except Exception as exception:
    if proxy['check']['tries_count'] < ATTEMPTS_TO_CHECK_LOCATION_COUNT:
     self.queue.put_nowait(proxy)
     do = 'Retry.'
    else:
     do = 'Stop checking.'
    logger.info(' '.join((
     f'Error to check proxy location for {proxy_str}. {do}',
     f'See exception message: {exception}'
    )))
   else:
    location_info = await response.json()
    proxy_location = location_info.get('country')
    proxy['fields']['location'] = proxy_locations.get(proxy_location.lower(), unknown_location)['locale']
    logger.info(f"Proxy {proxy_str} location detected. It's {proxy['fields']['location']}")
    response.close()
   finally:
    await session.close()
    self.queue.task_done()