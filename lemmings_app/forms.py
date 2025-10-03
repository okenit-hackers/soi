from django import forms
from django.utils.translation import gettext_lazy

from lemmings_app.models import LemmingsTask, BotAccount

from anon_app.models import Chain
from lemmings_app.utils import handle_bots_from_csv
from soi_app.utils import ImportBaseForm


class LemmingsTaskForm(forms.ModelForm):

 class Meta:
  model = LemmingsTask
  fields = '__all__'

 hom_much_to_create = forms.IntegerField(
  label=gettext_lazy('How much to create?'),
  initial=1,
  min_value=1,
 )


class BotAccountForm(forms.ModelForm):
 def __init__(self, *args, **kwargs):
  super(BotAccountForm, self).__init__(*args, **kwargs)
  self.fields['last_traceback'].strip = False
  self.fields['last_traceback'].widget.attrs['readonly'] = True

 class Meta:
  model = BotAccount
  fields = '__all__'


class AnonChainsForm(forms.Form):
 _selected_action = forms.CharField(widget=forms.MultipleHiddenInput)
 anon_chain = forms.ModelChoiceField(queryset=Chain.objects.filter(status=Chain.StatusChoice.READY.value),
          label='Доступные цепочки анонимизации')


class ImportBotAccountForm(ImportBaseForm):
 chain = forms.ModelChoiceField(queryset=Chain.objects.all(), label=gettext_lazy('chain'))

 def save(self):
  super(ImportBotAccountForm, self).save_base(handle_bots_from_csv)