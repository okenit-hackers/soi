from rest_framework.fields import ChoiceField


class TimezoneField(ChoiceField):
 """
 Take the timezone object and make it JSON serializable
 """

 def to_representation(self, obj):
  return str(obj)

 def to_internal_value(self, data):
  return data