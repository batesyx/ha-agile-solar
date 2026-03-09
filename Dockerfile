ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.20
FROM ${BUILD_FROM}

WORKDIR /app

# Install build dependencies for compiled packages
RUN apk add --no-cache gcc musl-dev

# Copy and install Python package
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

# Copy add-on run script (s6-overlay expects it here)
COPY addon/run.sh /etc/s6-overlay/s6-rc.d/octopus-export-optimizer/run
RUN chmod a+x /etc/s6-overlay/s6-rc.d/octopus-export-optimizer/run \
    && echo "longrun" > /etc/s6-overlay/s6-rc.d/octopus-export-optimizer/type \
    && mkdir -p /etc/s6-overlay/s6-rc.d/user/contents.d \
    && touch /etc/s6-overlay/s6-rc.d/user/contents.d/octopus-export-optimizer
