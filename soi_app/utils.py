import base64
import datetime
import logging
from urllib.parse import urljoin, urlparse

import requests
from django.db.models import TextChoices
from django import forms
from django.utils.translation import gettext_lazy
from lmgs_datasource.settings.main import AVAGEN_URL, AVAGEN_GET_RANDOM, AVAGEN_SSL_VERIFY


logger = logging.getLogger(__name__)


class ImportBaseForm(forms.Form):
 class FileTypeChoice(TextChoices):
  CSV = 'CSV', 'csv'

 class DelimiterChoice(TextChoices):
  tab = '\t', gettext_lazy('Tab symbol'),
  semicolon = ';', gettext_lazy('Semicolon'),
  colon = ':', gettext_lazy('Colon'),
  comma = ',', gettext_lazy('Comma'),

 file = forms.FileField(label=gettext_lazy('File'))
 file_type = forms.ChoiceField(label=gettext_lazy('File type'), choices=FileTypeChoice.choices)
 delimiter = forms.ChoiceField(label=gettext_lazy('Delimiter'), choices=DelimiterChoice.choices)

 def save_base(self, handle):
  in_file = self.cleaned_data['file'].file
  delimiter = self.cleaned_data['delimiter']
  file_type = self.cleaned_data['file_type']

  handler = {
   self.FileTypeChoice.CSV: handle
  }[file_type]

  handler(self, in_file, delimiter)


def get_birthday(age_value: float) -> datetime.date:
 """Получить день рождения по возрасту"""
 now = datetime.date.today()
 birth_year = datetime.date(year=now.year - int(age_value) - 1, month=now.month, day=now.day)
 lives_days_in_year = (age_value - int(age_value)) * 365
 return birth_year + datetime.timedelta(days=lives_days_in_year)


def get_random_avagen_photo() -> dict:
 """Получить случайное фото из авагена"""
 logger.info('Start to get random avagen photo')
 image_bs64 = None
 avagen_url = urljoin(AVAGEN_URL, AVAGEN_GET_RANDOM)
 response = requests.get(avagen_url, verify=AVAGEN_SSL_VERIFY).json()
 avatar_info = response['results'][0] if response.get('results') else response # raise IndexError

 # avaget return 0 for male and 1 for female
 sex = avatar_info['gender'] # raise KeyError

 age_value = avatar_info['age_value']
 date_of_birth = get_birthday(age_value)

 # fix url with port forwarding
 media_url = avatar_info['image']
 image_url = urljoin(AVAGEN_URL, urlparse(media_url).path)

 response = requests.get(image_url, verify=AVAGEN_SSL_VERIFY)
 if response.status_code == 200:
  image_bs64 = base64.b64encode(response.content)

 # response status 404 if random avatar was used
 avatar_info_url = urljoin(AVAGEN_URL, urlparse(avatar_info['url']).path)
 requests.patch(avatar_info_url, verify=AVAGEN_SSL_VERIFY, data={'is_used': True})

 return {
  'image_bs64': image_bs64,
  'sex': str(sex),
  'date_of_birth': date_of_birth,
 }
