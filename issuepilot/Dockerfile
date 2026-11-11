FROM python:3.12-slim AS build
RUN pip install --no-cache-dir uv==0.13.6
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src
ARG INCLUDE_SNOWFLAKE=false
RUN if [ "$INCLUDE_SNOWFLAKE" = "true" ]; then uv sync --frozen --no-dev --extra snowflake; else uv sync --frozen --no-dev; fi
FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=build --chown=app:app /app /app
COPY --chown=app:app policies ./policies
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app fixtures ./fixtures
COPY --chown=app:app warehouse ./warehouse
RUN mkdir -p data && chown app:app data
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 LANGGRAPH_STRICT_MSGPACK=true
USER app
EXPOSE 8000
CMD ["uvicorn", "issuepilot.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
