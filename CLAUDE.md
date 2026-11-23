# 콘타 모델 — 코드 수정 규칙

이 저장소는 **윈도우 다운로드판**만 개발합니다(웹 서버 배포 안 함). 자세한 배경은 README와 인수인계 문서.

## 기능별로 나뉜 코드 — 고칠 때 원칙

**하나를 고칠 때 다른 기능에 영향이 없도록, 기능마다 파일이 따로 있습니다.**

1. **고칠 기능의 파일만 고칩니다.** 아래 지도에서 파일을 찾고, 다른 파일은 꼭 필요할 때만 건드립니다.
2. **새 기능은 새 파일로.** 기존 파일에 끼워 넣지 말고 맞는 폴더에 파일을 하나 만듭니다.
3. **여러 곳이 같이 쓰는 코드는 공용 파일에만.** `model/layers.py`(상수), `model/mesh_tools.py`(메시 도구), `roads/style.py`(도로 색·치수). 공용 파일을 고치면 쓰는 곳 전체가 바뀌니 PR에 적고 전후 결과를 비교합니다.
4. **파일끼리 서로 불러오는 고리(순환 import)를 만들지 않습니다.** 방향은 공용 → 기능 → 흐름(`model/build.py`, `roads/assemble.py`) 한쪽으로만.
5. **파일은 700줄 이하.** 넘으면 기능별로 다시 나눕니다. 예외는 `tests/test_structure.py`의 목록에 있는 파일뿐이고, 줄여 나갑니다.
6. `contour_model.py`, `road_detail.py`는 **입구**(예전 이름으로 불러도 되게 이름만 다시 내보냄)입니다. 코드는 여기 쓰지 말고 `model/`, `roads/`에 씁니다.
7. 시험: `python -m pytest tests` 전부 통과 + 결과가 바뀌는 수정이면 전후 비교 그림(docs/확인그림/).

## 기능 지도

### 모델 본체 `contour_ai/model/`
| 파일 | 기능 |
|---|---|
| `layers.py` | 수치지도 레이어 코드, 층고·색·이름 상수 (공용) |
| `inputs.py` | 수치지도 DXF·SHP 읽기 |
| `terrain.py` | 등고선·표고점 보간, 계단 판 영역, 경사 높이장, 건물 터 |
| `surface.py` | 지형 메시, 길·면 얹기(드레이프), 메시 간소화 |
| `buildings.py` | 건물 덩어리(층수·높이) |
| `mesh_tools.py` | 저장 형식들이 같이 쓰는 메시 도구 (공용) |
| `export_3dm.py` | Rhino 3dm 저장 |
| `export_stl.py` | 3D 프린트 STL |
| `export_laser.py` | 레이저 재단 DXF 판 |
| `build_steps.py` | 만들기 단계 도우미(녹지·가로수·필지)와 기본 설정값 `DEFAULTS` |
| `build.py` | 전체 흐름 `build()` — 단계를 부르는 곳 |
| `live_preview.py` | 진행 화면 그림, 평면 미리보기 PNG |

### 도로 디테일 `contour_ai/roads/`
| 파일 | 기능 |
|---|---|
| `style.py` | 차로 폭, 페인트·시설물 색 (공용) |
| `data_files.py` | 횡단보도·신호등·전봇대 자료 파일 읽기 |
| `centerlines.py` | 도로 중심선 그래프, 차도 폭, 횡단 위치 |
| `junctions.py` | 교차로 면, 횡단보도 다듬기 |
| `islands.py` | 교통섬 |
| `lanes.py` | 차선·중앙선·정지선·화살표 |
| `aerial.py` | 항공사진 노면표시·횡단보도 |
| `street_furniture.py` | 신호등·전봇대 위치 |
| `parking.py` | 주차 구획 |
| `shapes.py` | 신호등·전봇대 3D 모양, 바닥 얇은 면 |
| `assemble.py` | `road_details()` — 출처 모아 우선순위 정리 |
| `check_image.py` | 노면표시 확인 그림 |

### 그 밖의 기능 (contour_ai/ 바로 아래, 파일 하나 = 기능 하나)
| 파일 | 기능 |
|---|---|
| `pipeline.py` | zip 하나로 모델 전체를 만드는 실행 흐름 |
| `app.py`, `web/` | 화면 서버와 화면 (나누기 남음 — 아래) |
| `site.py`, `sitefile.py` | 대지 경계·레벨, 대지 파일 정리 화면 |
| `pads.py` | 계단식 대지 |
| `sheets.py`, `dem.py` | 여러 도엽 잇기, DEM 높이 |
| `vegetation.py`, `aerial.py`, `tree_registry.py`, `tree_models.py`, `paths_trees.py` | 숲·항공사진 나무·가로수 대장·나무 모형·길 면과 가로수 |
| `building_detail.py`, `facade.py` | 건물 매스 디테일, 입면 창문·재료 |
| `structures.py`, `transit.py`, `underground.py`, `streetlights.py` | 고가·교량, 정류장·지하철 출입구, 땅속 시설, 가로등 |
| `hdmap_reader.py`, `hdmap_align.py`, `marking_assets.py`, `crosswalk_api.py` | 정밀도로지도, 노면표시 에셋, 횡단보도 API |
| `publicdata.py`, `landlaw.py` | 지적·건축물대장, 법규 검토 |
| `analysis.py`, `skp_export.py`, `progress.py` | 대지 분석, 스케치업 출력, 진행 신호 |

### 아직 나누지 않은 것
- `app.py`(1,300줄)와 `web/index.html`(1,400줄): 화면 서버와 화면. 시험(tests/test_stability.py)이 이 파일을 다시 불러오고 글자를 직접 찾는 방식이라, 나눌 때 시험도 같이 바꿔야 합니다.
- `model/build.py`의 `build()`는 한 함수가 500줄입니다. 단계 함수로 쪼개는 것은 다음 작업.
- `site.py`(850줄), `skp_export.py`(820줄).
