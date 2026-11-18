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
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt
COPY contour_ai ./contour_ai
RUN mkdir -p /data

EXPOSE 8080
CMD ["python", "-m", "contour_ai", "serve"]
