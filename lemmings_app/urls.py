from django.urls import include, path
from rest_framework import routers

from lemmings_app import views as lemmings_app_views

router = routers.DefaultRouter()
router.register(
 lemmings_app_views.LemmingsTaskViewSet.url_prefix,
 lemmings_app_views.LemmingsTaskViewSet
)
router.register(r'interval', lemmings_app_views.IntervalScheduleViewSet)
router.register(r'crontab_schedule', lemmings_app_views.CrontabScheduleViewSet)
router.register(r'solar_schedule', lemmings_app_views.SolarScheduleViewSet)
router.register(r'clocked_schedule', lemmings_app_views.ClockedScheduleViewSet)
router.register(r'behavior_bots', lemmings_app_views.BehaviorBotsViewSet)
router.register(r'botaccount', lemmings_app_views.BotAccountViewSet)

urlpatterns = [

 path('', include(router.urls)),
 path(
  f'{lemmings_app_views.CeleryTaskView.basename}/<uuid:{lemmings_app_views.CeleryTaskView.lookup_field}>/',
  lemmings_app_views.CeleryTaskView.as_view(), name=lemmings_app_views.CeleryTaskView.basename
 ),
 path(
  f'{lemmings_app_views.CeleryTaskView.basename}/', lemmings_app_views.CeleryTaskView.as_view(),
  name=lemmings_app_views.CeleryTaskView.basename
 ),
]