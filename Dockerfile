FROM python:3.13-alpine@sha256:c50ccfb66e5cc97a610dcd0b9597de6ef5b52c96280b51997b353a0fc87a8361
ARG VCS_REF=local
LABEL org.opencontainers.image.source="https://github.com/SamuraiSBS/localhost-3000"
LABEL org.opencontainers.image.revision=$VCS_REF
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=3000 APP_REVISION=$VCS_REF
WORKDIR /app
COPY --chown=65532:65532 infra/probe/server.py infra/probe/page.html ./
USER 65532:65532
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:3000/healthz', timeout=2).read()"
CMD ["python", "server.py"]
