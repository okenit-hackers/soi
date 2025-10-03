FROM maven.lan:8082/soi-openvpn

ENV PYTHONUNBUFFERED=1
ENV PATH="/soi:${PATH}"
ENV PYTHONPATH="${PYTHONPATH}:/soi"

ARG NEXUS_USER
ARG NEXUS_PASSWORD

WORKDIR /soi

RUN pip install --upgrade pip \
 && pip install 'setuptools<58.0.0' \
 && pip install pycrypto --no-cache

COPY pyproject.toml poetry.lock ./

RUN poetry config http-basic.nexus $NEXUS_USER $NEXUS_PASSWORD \
 && poetry install --no-ansi --no-interaction --no-cache --no-root

COPY . .

ENTRYPOINT ["entrypoint.sh"]