# Pinned by digest; bump deliberately (docker buildx imagetools inspect python:3.12-slim).
FROM python:3.12-slim@sha256:cb33f7f771ecec2ef1225cf39b159591a040391bd73e4c01f00fdfc4c47611d2
WORKDIR /app
# Dependencies come only from the hash-locked file. Regenerate with:
#   uv pip compile pyproject.toml --generate-hashes --universal --python-version 3.12 -o requirements.lock
COPY --chmod=0644 requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && useradd -r -u 10001 iotguard && mkdir /data && chown iotguard /data
# Run from source rather than pip-installing, so no unpinned build backend is fetched.
COPY --chmod=0755 iot_guard ./iot_guard
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER iotguard
ENTRYPOINT ["python", "-m", "iot_guard.main"]
CMD ["run"]
