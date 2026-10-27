FROM python:3.12-slim@sha256:229a2c5bfa27522db7815ea81f9bed70af17ccb9de9fc7ad142b1877b5830d36

LABEL org.opencontainers.image.source="https://github.com/DocMeThis/docmethis-check"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE /app/
COPY src /app/src
RUN python -m pip install --no-cache-dir \
      --index-url https://pkg.docmethis.com \
      --extra-index-url https://pypi.org/simple \
      . \
    && python -m pip check

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
