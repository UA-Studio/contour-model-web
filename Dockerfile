# 콘타 모델 공개 웹 서비스
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONTOUR_SERVER=1 \
    CONTOUR_DATA=/data \
    CONTOUR_HTTPS=1 \
    MPLBACKEND=Agg \
    PORT=8080

WORKDIR /app
# rasterio 휠이 시스템 libexpat 을 찾는다. python:3.13-slim (Debian 13) 새 판에서 빠져 '모델 만들기'가
# 'libexpat.so.1: cannot open shared object file' 로 멈췄다 (2026-11-19).
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt
COPY contour_ai ./contour_ai
RUN mkdir -p /data

EXPOSE 8080
CMD ["python", "-m", "contour_ai", "serve"]
