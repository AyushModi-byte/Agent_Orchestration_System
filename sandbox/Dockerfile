FROM python:3.11-slim
RUN pip install --no-cache-dir ruff bandit pytest \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /work
USER sandbox
