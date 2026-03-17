FROM python:3.12-alpine

WORKDIR /app

# Install build dependencies for compiled packages
RUN apk add --no-cache gcc musl-dev jq curl

# Copy and install Python package
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

# Copy add-on run script
COPY addon/run.sh /run.sh
RUN chmod a+x /run.sh

EXPOSE 8099

CMD ["/run.sh"]
