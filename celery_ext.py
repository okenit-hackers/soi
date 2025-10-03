from soi_tasks.core import app


if __name__ == '__main__':
 app.start(['-A', 'soi_tasks.core', 'worker', '-l', 'info', '-Q', 'external_celery'])