from soi_app.settings import DATABASES, EXTERNAL_APPS

class SoiRouter:
 """
 A router to control external_sos db operations
 """
 excluded_apps = [*EXTERNAL_APPS]

 def allow_migrate(self, db, app_label, model_name=None, **hints):
  if db in DATABASES and db == 'default':
   return app_label not in self.excluded_apps
  elif db == 'external_soi' and db in DATABASES:
   return app_label in self.excluded_apps
  return None