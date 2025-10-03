import json

from django.urls import reverse
from unittest.mock import patch

from anon_app.conf import settings

def _PATCH_finished_celery_task(*args, **kwargs):
 return True

# noinspection PyUnresolvedReferences
class ModelViewCreateTestMixin:
 def test_create(self):
  resp = self.client.post(
   reverse(self.view_url_name),
   data=json.dumps(self.__class__.data_generator()[1], ensure_ascii=False),
   content_type='application/json'
  )
  msg = f"resp: {resp.json()} | data: {self.last_instance_hyperlinked_data}"
  self.assertEqual(resp.status_code, 201, msg=msg)


# noinspection PyUnresolvedReferences
class ModelViewListTestMixin:
 def test_list(self):
  resp = self.client.get(f"{reverse(self.view_url_name).rstrip('/')}/?format=json")
  self.assertEqual(resp.status_code, 200)


# noinspection PyUnresolvedReferences
class ModelViewRetrieveTestMixin:
 def test_retrieve(self):
  url = f"{reverse(self.view_url_name).rstrip('/')}/{self.last_instance.id}/?format=json"
  resp = self.client.get(url)
  self.assertEqual(resp.status_code, 200)


# noinspection PyUnresolvedReferences
class ModelViewUpdateTestMixin:
 def test_update(self):
  self.assertIsNotNone(
   getattr(self, 'update_data_generator', None),
   msg=f'Set update_data_generator in {self.__class__.__name__} class'
  )
  resp = self.client.put(
   f"{reverse(self.view_url_name).rstrip('/')}/{self.last_instance.id}/?format=json",
   data=json.dumps(
    {**self.last_instance_hyperlinked_data, **self.__class__.update_data_generator()},
    ensure_ascii=False
   ), content_type='application/json'
  )
  msg = f"resp: {resp.json()} | data: {self.last_instance_hyperlinked_data}"
  self.assertEqual(resp.status_code, 200, msg=msg)


# noinspection PyUnresolvedReferences
class ModelViewPartialUpdateTestMixin:
 def test_partial_update(self):
  self.assertIsNotNone(
   getattr(self, 'update_data_generator', None),
   msg=f'Set update_data_generator in {self.__class__.__name__} class'
  )
  resp = self.client.patch(
   f"{reverse(self.view_url_name).rstrip('/')}/{self.last_instance.id}/?format=json",
   data=json.dumps(self.__class__.update_data_generator(), ensure_ascii=False),
   content_type='application/json'
  )
  self.assertEqual(resp.status_code, 200)


# noinspection PyUnresolvedReferences
class ModelViewDestroyTestMixin:
 @patch('anon_app.views.ChainView.is_task_finished', _PATCH_finished_celery_task)
 def test_destroy(self):
  resp = self.client.delete(
   f"{reverse(self.view_url_name).rstrip('/')}/{self.last_instance.id}/?format=json"
  )
  self.assertEqual(resp.status_code, 204)



# noinspection PyUnresolvedReferences,PyAttributeOutsideInit,PyPep8Naming
class ModelViewTestSetUpMixin:
 def setUp(self) -> None:
  self.last_instance_src_data, self.last_instance_hyperlinked_data = self.__class__.data_generator()
  self.last_instance = self.model.objects.create(**self.last_instance_src_data)
  if not self.is_logined:
   self.client.login(
    username=settings.ANON_APP_TEST_USER_NAME if not self.need_admin_user
    else settings.ANON_APP_TEST_SUPERUSER_NAME,
    password=settings.ANON_APP_TEST_USER_PASSWORD if not self.need_admin_user
    else settings.ANON_APP_TEST_SUPERUSER_PASSWORD,
   )
   self.is_logined = True


class ModelViewActionsTestMixin(
 ModelViewCreateTestMixin,
 ModelViewDestroyTestMixin,
 ModelViewListTestMixin,
 ModelViewRetrieveTestMixin,
 ModelViewUpdateTestMixin,
 ModelViewPartialUpdateTestMixin,
 ModelViewTestSetUpMixin
):
 pass