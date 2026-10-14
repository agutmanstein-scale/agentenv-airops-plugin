# The AirOps env: the airops-day MCP server with the b6-v1 pack, on port 18765.
#   Build: agent-env airops setup   (docker build -t agentenv-airops-env .)
FROM python:3.12-slim
LABEL org.opencontainers.image.source="https://github.com/earakely-scale/agentenv-airops-plugin" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.description="AirOpsEnv as an AgentEnv environment. Built from public U.S. records: BTS on-time and T-100 data, the FAA registry, FAA Command Center advisories and NWS METARs, TAFs and forecast discussions via the Iowa Environmental Mesonet."
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 AIROPS_TASKS_DIR=/app/data/b6-v1
RUN pip install "agentenv-framework-protocol==0.1.338" "fastmcp==3.4.8" "mcp==1.30.0" "pydantic==2.13.5" "tzdata"
WORKDIR /app
COPY LICENSE NOTICE /app/
COPY data /app/data
COPY src/agentenv_airops /app/agentenv_airops
RUN useradd --create-home --uid 1000 airops
USER airops
EXPOSE 18765
CMD ["python", "-m", "agentenv_airops.server"]
