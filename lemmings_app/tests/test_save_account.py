import unittest
from unittest.mock import MagicMock, patch
from django.test.testcases import TestCase

from anon_app.models import Proxy
from lemmings_app.tasks import save_account
from lemmings_app.models import BotAccount


class TestBotLocationSave(TestCase):
 fixtures = (
  "fixture_botaccount.json",
  "fixture_chain.json",
  "fixture_proxy.json",
  )

 def setUp(self):
  self.bot_with_proxy = 276
  self.bot_without_proxy = 277
  self.mock_task = MagicMock()

 @patch("lemmings_app.tasks.internal_app.task")
 def test_save_without_proxy(self, mock_task_decorator):
  mock_task_decorator.return_value = self.mock_task
  bot_extra = BotAccount.objects.get(pk=self.bot_without_proxy).extra
  previous_task_result = {
   "save_bot_info": "ok",
   "last_action": "save_bot_info",
   "extra": bot_extra
  }
  save_account(previous_task_result, self.bot_without_proxy)
  bot = BotAccount.objects.get(pk=self.bot_without_proxy)
  bot_location = bot.location
  self.assertEqual(bot_location, None)

 @patch("lemmings_app.tasks.internal_app.task")
 def test_save_proxy_with_location(self, mock_task_decorator):
  mock_task_decorator.return_value = self.mock_task
  bot_extra = BotAccount.objects.get(pk=self.bot_with_proxy).extra
  previous_task_result = {
   "save_bot_info": "ok",
   "last_action": "save_bot_info",
   "extra": bot_extra
  }
  save_account(previous_task_result, self.bot_with_proxy)
  bot = BotAccount.objects.get(pk=self.bot_with_proxy)
  bot_location = bot.location
  proxy_location = Proxy.objects.get(pk=bot.extra["proxy"]["current_proxy"]["pk"]).location
  self.assertEqual(bot_location, "Испания")
  self.assertEqual(bot_location, proxy_location)


if __name__ == "__main__":
 unittest.main()