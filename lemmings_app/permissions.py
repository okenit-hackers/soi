from rest_framework.permissions import IsAdminUser


class IsAdminOr(IsAdminUser):
 """
 Проверяет является ли пользователь админом ИЛИ выполняется ли иные проверки.
 Для добавления функций проверяющих что-либо еще нужно добавить их во множество
 IsAdminOr.additional_validators. Кроме того, функции должны реализовывать интерфейс

  def has_permission(request, view):
   ...

 """

 additional_validators = set()

 def has_permission(self, request, view):
  is_admin = super(IsAdminOr, self).has_permission(request, view)

  if is_admin:
   return True

  return any(validator(request, view) for validator in self.__class__.additional_validators)