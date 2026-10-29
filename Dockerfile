# Build from the repository root:  docker build -t mikazuki .
# remote-check and cvtracks are installed from their own repositories; job-feed and mikazuki from this one.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# git for the two dependencies; fonts for the PDF CVs (Typst finds system fonts)
RUN apt-get update \
 && apt-get install -y --no-install-recommends git fonts-liberation fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

ARG REMOTE_CHECK_REF=main
ARG CVTRACKS_REF=main
COPY job-feed /src/job-feed
COPY mikazuki /src/mikazuki
RUN pip install "remote-eligibility @ git+https://github.com/bloodmoonbreach/remote-check@${REMOTE_CHECK_REF}" \
                "cvtracks[pdf] @ git+https://github.com/bloodmoonbreach/cvtracks@${CVTRACKS_REF}" \
                /src/job-feed /src/mikazuki \
 && rm -rf /src && apt-get purge -y git && apt-get autoremove -y

ENV MIKAZUKI_DATA=/data MIKAZUKI_HOST=0.0.0.0 MIKAZUKI_PORT=8780 HOME=/tmp
VOLUME /data
EXPOSE 8780
USER 1000:1000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8780/healthz', timeout=4)"
CMD ["mikazuki"]
