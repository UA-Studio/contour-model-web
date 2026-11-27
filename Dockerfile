# 콘타 모델 웹 서버판 (fly.io). 윈도우판과 같은 코드를 CONTOUR_SERVER=1 로 띄운다.
# 만들기:  docker build -t contour-model .
# 띄우기:  docker run --rm -p 8080:8080 contour-model   → http://localhost:8080
FROM python:3.13-slim

ENV PYTHONUTF8=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    CONTOUR_SERVER=1 \
    CONTOUR_DATA=/data \
    PORT=8080

WORKDIR /app

# rasterio 휠의 GDAL 이 시스템 libexpat 을 찾음 (slim 이미지에는 없음)
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*

# 라이브러리 먼저 (코드만 바뀌면 이 층은 다시 받지 않음). rasterio·pyproj 휠에 GDAL·PROJ 가 들어 있음.
COPY requirements-server.txt .
RUN pip install --no-cache-dir -r requirements-server.txt

COPY contour_ai ./contour_ai

# 루트가 아닌 사용자로 실행. /data 는 올린 파일·결과 (쉬면 지워지는 임시 자리)
RUN useradd -m -u 1000 contour && mkdir -p /data && chown contour /data
USER contour

EXPOSE 8080
CMD ["python", "-m", "contour_ai", "serve"]
