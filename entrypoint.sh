#!/bin/bash
set -e
echo "soi: starting entrypoint.sh"

run() {
 echo "soi: run"
 gunicorn --timeout 90 --bind :8000 soi_app.wsgi
}

celery() {
 echo "sos:celery-external-worker start zabbix service [$1]"
 service zabbix-agent start
 echo "soi:celery-external-worker run [$1]"
 python -m celery -A soi_tasks.core worker -l info -Q "$1"
}

celery_internal_beat() {
 echo "soi:celery-internal-beat run"
 python -m celery -A soi_tasks.internal beat -l info
}

celery_internal() {
 echo "soi:celery-internal-worker:dbus specify config"
 dbus-daemon --config-file=/etc/dbus-1/accessibility.conf --print-address &
 echo "soi:celery-internal-worker:dbus starting daemon"
 dbus-daemon --system
 echo "soi:celery-internal-worker run"
 python -m celery -A soi_tasks.internal worker -l info -Q "$INTERNAL_CELERY_QUEUE_NAME"
}

celery_botfarm_internal() {
 echo "soi:celery-botfarm-internal-worker run"
 python -m celery -A soi_tasks.botfarm worker -l info -Q "$INTERNAL_CELERY_BOTFARM_QUEUE_NAME"
}

test() {
 echo "soi: test"
 echo "yes" | python manage.py test
}

case "$1" in

run)
 run
 ;;

celery)
 celery "$2"
 ;;

celery_internal_beat)
 celery_internal_beat
 ;;

celery_internal)
 celery_internal
 ;;

celery_botfarm_internal)
 celery_botfarm_internal
 ;;

test)
 test
 ;;

esac