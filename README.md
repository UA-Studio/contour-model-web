# 콘타 모델 만들기

국토정보플랫폼 **수치지도(수치지형도 2.0, SHP zip)** 를 넣으면 건축 **콘타 모형**을 자동으로 만들어 주는 프로그램입니다.
**윈도우 다운로드판**으로 씁니다. 파이썬 등 따로 설치할 것 없이 zip 을 풀고 `콘타모델 실행.bat` 을 더블클릭하면 브라우저에 화면이 열립니다.

**내려받기:** 이 저장소의 **Releases** 최신 판(`contour_model_windows_v버전_날짜.zip`)

**웹에서 바로 쓰기(다운로드 없이):** 같은 코드를 fly.io 서버에 띄운 웹 서버판도 있습니다. 설정 순서는 [docs/웹배포_안내.md](docs/웹배포_안내.md).

![대지 모델 3D 예시 (시험용 가짜 도엽)](docs/확인그림/3D_대지모델.png)

## 무엇을 만들어 주나

| 결과 | 쓰임 |
|---|---|
| 스케치업 (`model_sketchup.skp` / `.dae`) | **기본으로 만드는 모델 파일.** 지형, 건물(층수 기반 매스 + 건물 용도에 맞는 창문(창틀·창턱·유리 틴트·셔터·간판)·지붕 디테일, `건물::05층` 처럼 층수별 태그), 길·차선·횡단보도, 가로수·숲, 가로등, 고가·교량, 검토 대지(경계·레벨 등고선·레벨판·정북일조 사선, 태그 `대지::…`). 이 PC 에 스케치업이 있으면 .skp 까지 직접 |
| 3D 보기 (`model.glb`) | 결과 화면 3D 미리보기 (늘 만듦) |
| 2D 지형도 (`plan_2d.dxf`) | CAD 평면. 실제 좌표(m), 등고선(주곡선·계곡선, 높이 값)·건물(층수별)·도로·물·녹지·지적·대지 경계 레이어 |
| 사이트 다이어그램 (`site_diagram.svg`) | 일러스트레이터·피그마용. 주제별 레이어, 모형 축척 mm |
| 항공사진 지형 (`terrain_photo.obj`·`.glb`) | 항공사진이 있으면 사진을 입힌 지형 면. Blender·KeyShot·Twinmotion 렌더용 |
| 지형 단면 (`section_01.svg`·`.dxf`) | 모델을 만든 뒤 지도 탭에서 '단면선 긋기'로 두 점을 찍으면 그 선의 단면. 잘린 지형·건물은 진하게, 뒤쪽 깊이 안은 연하게, EL 눈금 |
| (명령줄 `--only` 로만) Rhino `model.3dm` · 3D 프린트 `model.stl` · 레이저 판 DXF | 코드는 남아 있음. 윈도우판 묶음에는 Rhino 라이브러리(rhino3dm)를 넣지 않아 Rhino 는 개발 환경에서만 |
| 평면 확인 그림, 대지 분석(경사·절성토) | 확인용 |
| `사용_안내.txt` | 파일마다 단위(m·mm)와 여는 법 |

**계단식 대지**(기본으로 켜짐): 비탈에 들어선 건물마다 평평한 터를 만들고, 옹벽·석축·담장·길로 둘러싸인 마당까지 같은 높이로 채웁니다. 터 사이와 터 둘레는 옹벽처럼 급하게 끊기고(옹벽 선 가까이는 거의 수직, 그 밖은 1:1 비탈), 높이 차 1m 안의 이웃 터는 한 단으로 합칩니다. 논은 한 필씩 평평하게, 비탈의 큰 논은 1.5m마다 다랑이로 나눕니다. 수치지도의 건물·옹벽(높이)·석축·절토·성토 선·담장·논·길 면을 씁니다(브이월드 3D 지형은 약관상 쓰지 않음). 끄려면 화면의 '계단식 대지' 스위치 또는 `--no-pads`.

**원형 범위**: 범위를 '원'으로 고르면 지름만큼 둥근 모형을 만듭니다(판·건물·길·STL 이 원에서 잘림). 명령줄은 `--circle`.

**받을 파일 고르기**: 화면에서는 스케치업(기본) · 2D 도면 · 사진 지형 중 필요한 것만 골라 더 빨리 끝냅니다(3D 보기는 항상). 명령줄은 `--only sketchup,plan` (개발 환경에서는 `rhino,laser,print` 도).

**해 그림자 보기**: 3D 보기의 '해 그림자' 버튼으로 춘분·하지·추분·동지, 6~19시 그림자를 보고 해 궤적을 켜고 그림으로 저장합니다(한국 시간, 모델 가운데 위도·경도). 설계 검토용이며 법정 일조 검토를 대신하지 않습니다.

대지 경계 파일(DXF·SHP·GeoJSON)을 함께 넣으면 대지를 가운데로 잡고 경계를 새기며, 도면에 계획 레벨(EL+178.00 같은 글자나 높이 있는 선)이 있으면 대지 안 지형을 그 레벨로 다시 만듭니다.

## 쓰는 법

1. [국토정보플랫폼](https://map.ngii.go.kr)에서 원하는 곳의 **수치지도 2.0(SHP)** 도엽 zip 을 받습니다. 대지가 두 도엽에 걸치면 둘 다 받습니다.
2. Releases 에서 윈도우판 zip 을 받아 **모두 압축 풀기** → `콘타모델_윈도우` 폴더의 **콘타모델 실행.bat** 더블클릭 (파란 'PC 보호' 창이 뜨면 추가 정보 → 실행).
3. 열린 화면에 도엽 zip 을 끌어다 놓습니다. 대지 도면 DXF 가 있으면 같이 놓습니다. 수치지도가 없으면 **예시 도엽으로 해 보기**(지어낸 지형)로 먼저 써 볼 수 있습니다.
4. 지도에서 범위를 고르고(대지를 넣으면 자동) **만들기**. 끝나면 3D 보기에서 지형·건물·길·나무 등을 하나씩 켜고 끄며 확인합니다. 결과는 `C:\Users\내이름\콘타모델` 에 저장됩니다.

64비트 Windows 10·11.

화면 기능 전체 설명은 [docs/사용법_전체.md](docs/사용법_전체.md) 에 있습니다.

## 넣지 않는 자료 (꼭 지킬 것)

- **정밀도로지도**(재배포 금지), **실제 수치지도 도엽**, **개인 사업지 도면** 은 저장소에 올리지 않습니다. 사용자가 자기 파일을 화면에서 올리는 방식입니다. `.gitignore` 가 zip·dxf·dwg·3dm 등을 막아 둡니다.
- **API 인증키**(브이월드, 공공데이터포털 등)는 코드·저장소에 쓰지 않습니다. 윈도우판에서는 각자 화면의 설정 칸에 자기 키를 넣습니다.

## 개발 방식

- **개선은 브랜치에서 하고 PR 로 올립니다.** PR 에는 바뀐 점, 시험 결과, 확인 그림을 담습니다.
- **자동 시험** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): 커밋을 올릴 때마다 Windows 와 Linux 에서
  1. 설치 (`requirements-server.txt`, 버전 고정)
  2. 가짜 도엽(`tests/make_fake_sheet.py`, 지어낸 지형·건물·길)으로 300m 모델 두 벌 만들기: 기본 모델, 대지 경계 + 레벨 도면 + 대지 비우기 모델
  3. 결과 확인: Rhino 파일 열림, 레이저 판 DXF 열림, STL 닫힌 솔리드·실제 축척, GLB 열림
  4. 화면(웹 서버 모드 포함)이 열리는지
  5. 확인 그림(3D · 평면 · 레이저 판)을 Actions 실행 화면의 **Artifacts** 로 남김 (Linux 는 결과 모델 파일도)
- **윈도우판 배포** ([`.github/workflows/windows.yml`](.github/workflows/windows.yml)): Windows 에서 zip 을 묶고, 새 폴더에 풀어 `.bat` 으로 가짜 도엽 모델을 두 번 만들어 봅니다. `main` 이고 그 버전(`contour_ai/__init__.py`)의 릴리스가 없으면 **Releases** 에 올립니다. 새 판을 내려면 버전을 올리세요.

## 폴더

```
contour_ai/          프로그램 본체 (같은 코드로 공개 서버와 PC 화면을 모두 돌림)
  web/               화면 (HTML · JS)
  data/              내장 가로수 대장 (공개 자료)
tests/               자동 시험 (가짜 도엽 생성기, 모델·서버 시험, 확인 그림)
docs/                전체 사용법, 예전 개발용 시험 스크립트, README 그림
windows/             윈도우판 묶기 스크립트·실행 .bat·안내문
```

### 무엇을 고치려면 어느 파일을 (기능별로 나뉘어 있음)

한 파일을 고쳐도 다른 기능에는 영향이 없도록 기능마다 파일을 나눠 두었습니다. 고칠 것에 맞는 파일만 열면 됩니다. 고칠 때 지킬 규칙과 전체 지도는 [CLAUDE.md](CLAUDE.md) 에 있습니다.

| 고칠 것 | 파일 (`contour_ai/` 안) |
|---|---|
| 모델 만들기 전체 순서 (어떤 단계를 어떤 순서로) | `build_flow.py`: `build` 가 단계 함수를 차례로 부름 · 단계는 `build_prepare.py`(지형) · `build_buildings.py`(건물) · `build_streets.py`(길·나무·도로) · `build_surface.py`(얹기·경사·대지 표시) · `build_save.py`(저장·요약) · `pipeline.py` |
| 기본 설정값 / 받을 파일 종류(기본 스케치업만) | `build_defaults.py` / `output_kinds.py` |
| 수치지도 읽기 (레이어 코드, SHP·DXF) | `mapdata.py` |
| 땅 모양 (등고선 → 높이, 계단 판, 경사면, 건물 터) | `terrain.py` · 계단식 대지 `pads.py` · DEM `dem.py` |
| 건물 덩어리 (층수 → 높이, 용도 색) | `buildings.py` · 창문·지붕 `building_detail.py` · 외관 `facade.py` |
| 건물 용도별 창 짜기 (빌라·띠창·판상형 발코니·학교·1층 상가, 창틀·창턱·유리 틴트·간판) | `facade_windows.py` |
| 길·면을 땅 위에 얹기 | `drape.py` · 길 종류·나무 모양 `paths_trees.py` |
| 나무·숲 | `greens.py` · `vegetation.py` · `tree_registry.py` · `tree_models.py` |
| 도로 디테일 전체 순서 | `road_detail.py` (`road_details`) |
| ㄴ 길 뼈대 (중심선·교차로) | `road_network.py` |
| ㄴ 횡단보도 | `road_crosswalks.py` |
| ㄴ 차선·정지선·화살표 | `road_marks.py` |
| ㄴ 신호등·전봇대 | `road_fixtures.py` |
| ㄴ 교통섬 / 주차 구획 / 자료 파일 | `road_islands.py` / `road_parking.py` / `road_sources.py` |
| 결과 파일: Rhino / STL / 레이저 판 / 스케치업 | `out_rhino.py` / `out_stl.py` / `out_laser.py` / `skp_write.py`(.dae 바탕 `skp_dae.py` · 확장 Ruby `skp_ruby.py` · 스케치업 실행 `skp_app.py`) |
| 2D 지형도 DXF · 사이트 다이어그램 SVG / 영역별 지형(도로·보도·녹지·물·대지) | `out_plan.py` / `terrain_zones.py` |
| 평면 확인 그림 / 만드는 중 화면 그림 | `preview.py` / `live_view.py` |
| 대지 경계·사업지 도면 | 파일 읽기 `site_read.py` · 도면 좌표 맞추기 `site_align.py` · 불러오기·레벨 `site_load.py` · 레벨 지형 `site_levels.py` · 대지 위 비우기 `site_cut.py` · 정리 `sitefile.py` |
| 결과 폴더 사용 안내(단위·여는 법) / 예시 도엽 | `guide.py` / `sample_sheet.py` |
| 서버·화면 주고받기 | 요청 처리 `app_http.py` · 만들기 요청 `app_start.py` · 작업 `app_jobs.py` · 결과 `app_results.py` · 설정·상태 `app_state.py` · 켜기 `app_server.py` · 지도 그림 `app_mapview.py` · 대지 파일 정리 `app_siteprep.py` · 작은 도구 `app_util.py` |

화면(`contour_ai/web/`)도 칸별로 나뉘어 있습니다: 모양은 `app.css`, 동작은 `ui_files.js`(파일 넣기) · `ui_site.js`(대지) · `ui_prep.js`(대지 파일 정리) · `ui_map.js`(범위·지도) · `ui_run.js`(만들기·진행) · `ui_results.js`(결과) · `ui_errors.js`(오류 안내) · `ui_view3d.js`(3D 보기) · `ui_presets.js`(왼쪽 설정) · `ui_server.js`(공개 서버·의견 보내기). 여러 칸이 함께 쓰는 값은 `ui_common.js` 의 `st` 에 모여 있습니다.

## 직접 돌려 보기 (개발자용)

```
pip install -r requirements-server.txt pytest
python -m pytest tests -v              # 시험
python tests/render_previews.py        # 시험결과/확인그림/
python -m contour_ai                   # PC 화면
CONTOUR_SERVER=1 python -m contour_ai serve   # 공개 서버 모드
```
