import unittest
from django.test.testcases import TestCase

from anon_app.models import Proxy, Chain


class TestChainSelectProxy(TestCase):
 fixtures = (
  "fixture_botaccount.json",
  "fixture_chain.json",
  "fixture_proxy.json",
  )

 def setUp(self):
  self.bot_with_proxy = 278
  self.bot_without_proxy = 277
  self.chain = Chain.objects.get(pk=12)
  return super().setUp()

 def test_select_proxy_for_bot_with_location(self):
  result = self.chain.get_alive_proxies_query_with_conditions(bot_pk=self.bot_with_proxy)
  self.assertEqual(result[0].location, "Россия")
  self.assertIsInstance(result[0], Proxy)

 def test_select_proxy_for_bot_without_location(self):
  container = ("Россия", "Испания", "Германия", "Бельгия", "Соединенные Штаты")
  result = self.chain.get_alive_proxies_query_with_conditions(bot_pk=self.bot_without_proxy)
  for proxy in result:
   with self.subTest():
    self.assertIn(proxy.location, container)
    self.assertIsInstance(proxy, Proxy)


if __name__ == "__main__":
 unittest.main()