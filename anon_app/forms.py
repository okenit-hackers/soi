import json
import random

from django import forms
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.core import serializers
from django.utils.translation import gettext_lazy

from anon_app.models import Chain, Proxy
from anon_app.tasks.tasks import rebuild_proxychains4
from anon_app.utils import handle_proxies_from_csv
from soi_app.utils import ImportBaseForm

# Отображаем в форме только те протоколы, которые необходимы при проверке
FORM_PROTOCOL_CHOICES: list = Proxy.ProtocolChoice.choices
FORM_PROTOCOL_CHOICES.remove((Proxy.ProtocolChoice.UNKNOWN_PROTOCOL, Proxy.ProtocolChoice.UNKNOWN_PROTOCOL.label))
FORM_PROTOCOL_CHOICES.remove((Proxy.ProtocolChoice.EMPTY, Proxy.ProtocolChoice.EMPTY.label))


def convert_proxy_for_config(proxy) -> str:
 """Convert proxy to proxychains4 configuration format.

 :param proxy: serialized proxy object
 :returns: converted_proxy - proxy in suitable format for proxychains4.conf
 """
 attributes = proxy['fields']
 protocol = attributes['protocol']
 ip = attributes['ip']
 port = attributes['port']
 username = attributes.get('username')
 password = attributes.get('password')

 if username and password:
  return f'{protocol} {ip} {port} {username} {password}'
 return f'{protocol} {ip} {port}'


class ImportProxiesForm(ImportBaseForm):
 import_csv_format = forms.ChoiceField(
  label=gettext_lazy('CSV format'), choices=Proxy.ImportCsvFormatChoice.choices
 )
 protocol = forms.ChoiceField(
  label=gettext_lazy('Protocol'), choices=FORM_PROTOCOL_CHOICES, required=False
 )
 check_proxies = forms.BooleanField(label=gettext_lazy('Check proxies'), required=False)
 chain = forms.ModelChoiceField(queryset=Chain.objects.all(), label=gettext_lazy('Chain for check'), required=False)
 secure_flag = forms.ChoiceField(
  label=gettext_lazy('Secure flag'), required=False, choices=Proxy.SecureFlagChoice.choices
 )
 number_of_applying = forms.ChoiceField(
  label=gettext_lazy('Number of applying'), required=False, choices=Proxy.NumberOfApplyingChoice.choices
 )
 source = forms.CharField(
  max_length=128, label=gettext_lazy('source'), required=False
 )
 comment = forms.CharField(
  widget=forms.Textarea,
  label=gettext_lazy('Comment'),
  required=False,
 )

 def save(self):
  in_file = self.cleaned_data['file'].file
  delimiter = self.cleaned_data['delimiter']
  file_type = self.cleaned_data['file_type']
  csv_format = self.cleaned_data['import_csv_format']
  protocol = self.cleaned_data['protocol']
  anon_chain = self.cleaned_data['chain']
  secure_flag = self.cleaned_data['secure_flag']
  number_of_applying = self.cleaned_data['number_of_applying']
  applying = Proxy.ApplyingChoice.UNUSED
  source = self.cleaned_data['source']
  comment = self.cleaned_data['comment']

  handler = {
   self.FileTypeChoice.CSV: handle_proxies_from_csv
  }[file_type]

  handler(
   in_file, delimiter, csv_format,
   protocol, secure_flag, number_of_applying,
   applying, source, comment, anon_chain
  )


# https://stackoverflow.com/questions/59302784/django-modelmultiplechoicefield-lazy-loading-of-related-m2m-objects
class ChainAdminForm(forms.ModelForm):
 class Meta:
  model = Chain
  exclude = []

 proxy = forms.ModelMultipleChoiceField(
  label=gettext_lazy('Proxy'),
  queryset=Proxy.objects.get_alive_proxies().filter(chain__isnull=True),
  required=False,
  widget=FilteredSelectMultiple('proxy', False)
 )

 def __init__(self, *args, **kwargs):
  super(ChainAdminForm, self).__init__(*args, **kwargs)
  if self.instance.pk:
   self.fields['proxy'].initial = self.instance.proxy_set.all()
   self.fields['proxy'].queryset = Proxy.objects.filter(
    chain=self.instance.pk) | Proxy.objects.get_alive_proxies().filter(chain__isnull=True)

 def _save_m2m(self):
  save = super(ChainAdminForm, self)._save_m2m()
  self.instance.save()
  self.instance.proxy_set.set(self.cleaned_data['proxy'])
  return save

 def save(self, commit=True):
  instance = super(ChainAdminForm, self).save(commit)
  self._save_m2m()
  return instance

 def clean(self):
  """Customized method for checking data inputted in form fields.
  Check if selected proxies is consistent with attribute proxies_in_chain.
  Also check and start rebuild proxychains if proxies were changed for proxychain.
  Raises:
   ValidationError: in case of inconsistency of chosen proxies with proxies amount in chain.
  """
  choosen_proxies = self.cleaned_data['proxy']
  proxies_in_chain = self.cleaned_data['proxies_in_chain']
  are_proxies_changed = 'proxy' in self.changed_data
  is_proxies_chain = self.instance.has_proxies_chain

  if len(choosen_proxies) < proxies_in_chain:
   raise forms.ValidationError(
    'Количество выбранных прокси должно быть не меньше атрибута "количество прокси серверов в цепочке"',
   )

  if are_proxies_changed and is_proxies_chain:
   serialized_proxies = json.loads(serializers.serialize('json', choosen_proxies))
   converted_proxies = list(map(convert_proxy_for_config, serialized_proxies))
   proxies = random.sample(converted_proxies, proxies_in_chain)
   rebuild_proxychains4.delay(
    chain_id=self.instance.pk, is_internal=True, proxies=proxies,
    task_identifier=f'rebuild:proxychains4:{self.instance.pk}',
   )