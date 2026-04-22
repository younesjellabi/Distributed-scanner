# Phase 1 scanner — probe-only
# L1: UID 1000 non-root, CAP_NET_RAW only
# L2: read-only rootfs at runtime, /data/scans via volume
# L3: PYTHONUNBUFFERED=1 for immediate stderr flushing
# L4: signal handler in probe.py
# L5: tini as PID 1

FROM python:3.11-alpine

RUN apk add --no-cache tini

RUN addgroup -S scanner && \
    adduser  -S scanner -G scanner -u 1000

ENV PYTHONUNBUFFERED=1

RUN mkdir -p /data/scans && \
    chown scanner:scanner /data/scans

COPY --chown=scanner:scanner app/probe.py      /app/probe.py
COPY --chown=scanner:scanner app/inventory.json /app/inventory.json

USER 1000

ENTRYPOINT ["/sbin/tini", "--", "python", "/app/probe.py"]
