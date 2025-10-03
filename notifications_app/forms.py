from django import forms

from notifications_app.models import Notification


class NotificationForm(forms.ModelForm):
 def __init__(self, *args, **kwargs):
  super(NotificationForm, self).__init__(*args, **kwargs)
  self.fields['traceback'].strip = False
  self.fields['traceback'].widget.attrs['readonly'] = True

 class Meta:
  model = Notification
  fields = '__all__'