FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY iot_guard ./iot_guard
RUN pip install --no-cache-dir . && useradd -r -u 10001 iotguard && mkdir /data && chown iotguard /data
USER iotguard
ENTRYPOINT ["iot-guard"]
CMD ["run"]
