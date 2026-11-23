@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 콘타 모델 만들기
rem 다른 프로그램(QGIS, ArcGIS, Anaconda 등)이 깔아 둔 설정이 이 프로그램을 방해하지 않게 비운다
set PYTHONHOME=
set PYTHONPATH=
set PYTHONSTARTUP=
set PROJ_LIB=
set PROJ_DATA=
set GDAL_DATA=
set GDAL_DRIVER_PATH=
set PYTHONNOUSERSITE=1
set PYTHONUTF8=1
rem 파이썬 캐시(.pyc)를 프로그램 폴더가 아닌 내 PC 앱 자료 폴더에 둔다: 원드라이브·읽기 전용 폴더에서도
rem 두 번째 실행부터 빨리 켜지고, 프로그램 폴더에 파일 수천 개가 새로 생기지 않는다
if defined LOCALAPPDATA set "PYTHONPYCACHEPREFIX=%LOCALAPPDATA%\콘타모델\pycache"
if not exist "%~dp0python\python.exe" (
  echo 압축이 다 풀리지 않았습니다. zip 파일을 오른쪽 클릭 - "모두 압축 풀기" 한 뒤 풀린 폴더에서 실행하세요.
  pause
  exit /b 1
)
echo 브라우저에 화면이 열립니다. 이 검은 창을 닫으면 프로그램이 꺼집니다.
"%~dp0python\python.exe" -s -m contour_ai %*
pause
