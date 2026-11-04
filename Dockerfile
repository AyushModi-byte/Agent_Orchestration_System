# One image for api, worker and beat; compose picks the command.
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
# deps first so code changes do not reinstall them
COPY pyproject.toml ./
RUN mkdir -p src/conductor && touch src/conductor/__init__.py  && pip install --no-cache-dir -e . && rm -rf src/conductor
COPY src ./src
COPY config ./config
COPY demo ./demo
# editable install keeps config/ next to the package, which config.py resolves relative to src/
RUN pip install --no-cache-dir --no-deps -e .  && useradd --uid 10002 --create-home conductor && mkdir -p /app/.cache && chown conductor /app/.cache
USER conductor
EXPOSE 8000
CMD ["uvicorn", "conductor.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
