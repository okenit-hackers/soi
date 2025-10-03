from soi_tasks.internal import app


if __name__ == '__main__':
 app.start(['-A', 'soi_tasks.internal', 'worker', '-B', '-l', 'info', '--scheduler', 'django_celery_beat.schedulers:DatabaseScheduler', '-Q', 'internal_celery'])