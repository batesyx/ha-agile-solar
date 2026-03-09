ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.20
FROM ${BUILD_FROM}

WORKDIR /app

# Install build dependencies for compiled packages
RUN apk add --no-cache gcc musl-dev jq curl

# Copy and install Python package
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

# Copy add-on run script
COPY addon/run.sh /
RUN chmod a+x /run.sh
