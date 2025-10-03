all: help

help:
 @echo "mmigrate ----------- - Generate and use migrations."
 @echo " migrate ---------- - Use migrations."
 @echo " migrations ------- - Generate migrations."
 @echo "test --------------- - Run unittests using current python."
 @echo "dist --------------- - Rebuild."
 @echo " clean ------------ - Clean all distribution build artifacts."
 @echo " clean-pyc ------ - Remove .pyc/__pycache__ files."
 @echo " clean-build ---- - Remove setup artifacts."
 @echo " build ------------ - Regenerate setup.py and rebuild python package."
 @echo " locale --------- - Regenerate locale files."
 @echo "upload ------------- - Upload built python package on pypi server."
 @echo "run ---------------- - Run dev server."

migrations:
 python manage.py makemigrations

migrate:
 python manage.py migrate

mmigrate: migrations migrate

mmigrate: migrations migrate

test: clean
 TEST=true python manage.py test

clean-pyc:
 -find . -type f -a \( -name "*.pyc" -o -name "*$$py.class" \) | xargs rm
 -find . -type d -name "__pycache__" | xargs rm -r

clean-build:
 rm -rf build/ dist/ .eggs/ *.egg-info/

clean: clean-pyc clean-build

locale:
 python manage.py compilemessages --locale ru

dist: clean build

run:
 python manage.py runserver