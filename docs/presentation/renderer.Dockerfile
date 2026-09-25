FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-impress fonts-liberation fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["libreoffice"]
