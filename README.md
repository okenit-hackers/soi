# SOI

Программный комплекс сокрытия интереса.

## Комментарии к структуре проекта

Зависимости находятся в каталоге `requirements`.

В `requirements/dev.in` находятся пакеты, необходимые только для разработчика.
Ни для тестов, ни для дебага, ни для релиза они не нужны.

В `requirements/testing.in` находятся зависимости, необходимые для тестирования.

В `requirements/requirements.in` находятся зависимости, необходимые для
функционирования проекта.

В requirements.txt находится результат работы команды

```shell script
pip-compile --index-url=http://192.168.5.131:8282/simple/ --output-file=requirements.txt --trusted-host=192.168.5.131 requirements/requirements.in requirements/testing.in
```

Подробнее тут: [pip-tools](https://github.com/jazzband/pip-tools).

Пакет можно установить с помощью команды

``` shell script
pip install --index-url=http://192.168.5.131:8282/simple/ --trusted-host=192.168.5.131 soi-okenit
```

## Сборка

[Сборка установочного пакета](https://gitlab.lan/filigree/sos/wikis/%D0%A1%D0%B1%D0%BE%D1%80%D0%BA%D0%B0-%D1%83%D1%81%D1%82%D0%B0%D0%BD%D0%BE%D0%B2%D0%BE%D1%87%D0%BD%D0%BE%D0%B3%D0%BE-%D0%BF%D0%B0%D0%BA%D0%B5%D1%82%D0%B0)
идентична сборке пакета для sos.

## Первый запуск dev-проекта
https://gitlab.lan/filigree/soi/-/wikis/%D0%97%D0%B0%D0%BF%D1%83%D1%81%D0%BA-dev-%D0%BF%D1%80%D0%BE%D0%B5%D0%BA%D1%82%D0%B0