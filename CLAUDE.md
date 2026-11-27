# 콘타 모델 — 코드 수정 규칙

이 저장소는 **윈도우 다운로드판**(스케치업 전용)을 개발합니다. 웹 서버로 배포하지 않습니다.
코드는 원래 저장소(juhyeonglee-arch/contour-model-web)에서 주기적으로 가져옵니다(`sync-v*` 브랜치).

## 고칠 때 원칙: 하나를 고쳐도 다른 기능에 영향이 없게

1. **고칠 기능의 파일만 고칩니다.** 아래 지도에서 파일을 찾고, 다른 파일은 꼭 필요할 때만 건드립니다.
2. **새 기능은 새 파일로.** 기존 파일에 끼워 넣지 말고, 같은 무리의 앞이름(`build_`, `site_`, `skp_`, `app_`, `road_`, `out_`)을 붙여 `contour_ai/` 바로 아래에 파일을 하나 만듭니다. (원래 저장소처럼 하위 폴더 없이 한 폴더)
3. **여러 곳이 같이 쓰는 코드는 공용 파일에만** 둡니다. 공용 파일을 고치면 쓰는 곳이 모두 바뀌므로 PR 에 적고, 아래 7번의 전후 비교를 합니다.
   - 모델: `build_defaults.py`(기본 설정값), `mapdata.py`(레이어 코드), `geom_util.py`(도형 도구), `mesh_shading.py`(메시 음영)
   - 화면 서버: `app_state.py`(설정·방문자 칸·작업 줄) — 다른 `app_*.py` 는 값을 `S.이름` 으로 씁니다 (`from . import app_state as S`). 값을 `from .app_state import LIMITS` 처럼 복사해 오지 않습니다.
4. **파일끼리 서로 불러오는 고리(순환 import)를 만들지 않습니다.** 방향은 공용 → 기능 → 흐름 한쪽으로만. (예: `build_defaults` → `build_prepare` … → `build_flow`, `app_state` → `app_jobs` → `app_start` → `app_http` → `app_server`)
5. **파일은 700줄 이하.** 넘으면 기능별로 다시 나눕니다. 예외는 없습니다 (`web/` 의 html·js·css 도 같음).
6. **입구 파일에는 코드를 쓰지 않습니다.** `contour_model.py`, `site.py`, `skp_export.py`, `app.py` 는 예전 이름으로 불러도 동작하도록 이름만 다시 내보냅니다. 새 이름을 옮기거나 만들면 입구에도 한 줄 더합니다.
7. **시험**
   - `python -m pytest tests` 전부 통과 (`tests/test_structure.py` 가 1·4·5·6번과 `S.이름` 규칙을 확인)
   - 코드를 옮기거나 공용 파일을 고칠 때는 결과가 그대로인지 비교:
     ```
     CONTOUR_TEST_OUT=전 python -m pytest tests     # 고치기 전 (main)
     CONTOUR_TEST_OUT=후 python -m pytest tests     # 고친 뒤
     python tests/compare_outputs.py 전 후          # '같음 N개' 가 나와야 함
     ```
   - 결과가 바뀌는 수정이면 전후 비교 그림(docs/확인그림/)을 PR 에 붙입니다.
8. 원래 저장소에서 가져올 때(sync): 원래 저장소가 아직 큰 파일(`contour_model.py` 등)을 고쳤으면, 바뀐 함수를 이 저장소의 나뉜 파일(아래 지도)에 옮겨 적용합니다. 함수 이름은 그대로라 `grep -n "def 함수이름" contour_ai/*.py` 로 찾을 수 있습니다.

## 기능 지도 (`contour_ai/`)

### 모델 만들기 흐름 — `build_*.py` (입구 `contour_model.py`)
| 파일 | 기능 |
|---|---|
| `build_flow.py` | `build()` 전체 순서 — 단계 함수를 차례로 부름, 명령줄 `main()` |
| `build_defaults.py` | 기본 설정값 `DEFAULTS`, 창틀 범위 `FINE_TRIM_RADIUS` (공용) |
| `output_kinds.py` | 받을 파일 종류 `OUTPUT_NAMES`, 기본값 `OUTPUTS_DEFAULT`(스케치업만), `_outputs()` |
| `build_prepare.py` | 1~2단계: 설정·수치지도 읽기, 높이 보간, 계단식 대지, 사업지 레벨, 계단판 |
| `build_buildings.py` | 3단계: 건물 매스 앉히기, 건물 디테일(입면·창문) |
| `build_streets.py` | 4단계: 길·면, 나무, 가로등, 도로 디테일, 거리 실황 |
| `build_surface.py` | 5~6단계: 길 면 얹기, 경사 지형, 원형 범위, 대지 표시, 영역별 지형 |
| `build_save.py` | 7~8단계: 파일 저장(Rhino·STL·스케치업·레이저·2D·사진 지형·단면), 요약 |
| `pipeline.py` | zip 하나로 모델 전체를 만드는 실행 흐름(도엽 잇기·자료 받기 → `build()`) |

### 모델 부품
| 파일 | 기능 |
|---|---|
| `mapdata.py` | 수치지도 SHP·DXF 읽기, 레이어 코드 (공용) |
| `terrain.py`, `pads.py`, `dem.py`, `sheets.py`, `terrain_zones.py`, `circle_range.py` | 지형·계단판, 계단식 대지, DEM, 여러 도엽 잇기, 영역별 지형, 원형 범위 |
| `buildings.py`, `building_detail.py`, `facade.py`, `facade_windows.py` | 건물 매스, 매스 디테일, 입면 재료, 용도별 창 |
| `drape.py`, `paths_trees.py` | 길·면을 땅 위에 얹기, 길 면·가로수 |
| `greens.py`, `vegetation.py`, `canopy_height.py`, `aerial.py`, `tree_registry.py`, `tree_models.py` | 녹지·숲·나무 |
| `road_detail.py` + `road_*.py` | 도로 디테일 모으기 + 길 뼈대·횡단보도·차선·신호등·교통섬·주차·자료 파일 |
| `hdmap_reader.py`, `hdmap_align.py`, `marking_assets.py`, `crosswalk_api.py` | 정밀도로지도, 노면표시 에셋, 횡단보도 API |
| `structures.py`, `transit.py`, `osm_transit.py`, `busstop_api.py`, `underground.py`, `streetlights.py` | 고가·교량, 정류장·지하철, 땅속 시설, 가로등 |
| `publicdata.py`, `landlaw.py`, `analysis.py` | 지적·건축물대장, 법규 검토, 대지 분석 |
| `geom_util.py`, `mesh_shading.py` | 도형 도구, 메시 음영 (공용) |

### 결과 파일
| 파일 | 기능 |
|---|---|
| `skp_write.py` | 스케치업 `write_sketchup()` — 레이어별 그룹·건물·나무 (입구 `skp_export.py`) |
| `skp_dae.py` | COLLADA(.dae) 쓰기 `Dae`, 건물 정보 JSON `SkpJson` |
| `skp_ruby.py` | 스케치업 확장(.rbz) Ruby 코드, 여는 법 안내 글 |
| `skp_app.py` | 내 PC 스케치업 찾기, .skp 바로 만들기 |
| `out_rhino.py`, `out_stl.py`, `out_laser.py`, `out_plan.py`, `photo_terrain.py`, `section.py` | Rhino, STL, 레이저 판, 2D 지형도·다이어그램, 사진 지형, 단면 |
| `site_overlay.py`, `guide.py`, `preview.py`, `live_view.py`, `progress.py` | 대지 표시 도형, 사용 안내, 평면 그림, 진행 화면 그림·신호 |

### 대지 — `site_*.py` (입구 `site.py`)
| 파일 | 기능 |
|---|---|
| `site_read.py` | 경계 파일 읽기(DXF·SHP·GeoJSON·찍은 점), `SiteError`, 공용 정규식 |
| `site_align.py` | 도면 좌표 → 수치지도 좌표 (표고점 맞추기) |
| `site_load.py` | 좌표계 고르기 `load_boundary`, 도면 레벨, 사업지 파일 전체 `load_site_file` |
| `site_levels.py` | 사업지 레벨로 대지 안 지형 만들기, 경계 밖과 섞기 |
| `site_cut.py` | 대지 위 비우기(건물·면·선·도로 자르기), 대지 윤곽 메시 |
| `sitefile.py` | 대지 파일 정리 → 표준 대지 파일 |

### 화면 서버 — `app_*.py` (입구 `app.py`), 화면 `web/`
| 파일 | 기능 |
|---|---|
| `app_state.py` | 설정·제한·방문자 칸·작업 줄 (공용, `S.이름`) |
| `app_http.py` | HTTP 요청 처리 `Handler` (화면 파일·상태·올리기·만들기·내려받기) |
| `app_start.py` | '만들기' 요청 → 작업 설정, 입력·서버 제한 확인 |
| `app_jobs.py` | 작업 `Job` 실행·멈추기·시간 제한, 서버 모드 따로 된 프로세스, 대기 줄 |
| `app_results.py` | 보여 줄 결과 폴더, 최근 결과, 예시 도엽 zip |
| `app_inputs.py` | 넣은 수치지도 살펴보기, 대지 파일 저장 |
| `app_sessions.py` | 서버: 쉬는 칸 지우기, 디스크 자리 만들기 |
| `app_admin.py` | 서버: 의견 보내기, 개발자 열쇠, 받은 의견 보기 |
| `app_server.py` | 서버 켜기: 내 PC `main()`, 공개 서버 `serve()` |
| `app_mapview.py`, `app_siteprep.py`, `app_util.py`, `app_lazy.py` | 지도 그림, 대지 파일 정리 화면, 작은 도구, 늦게 불러오기 |
| `web/` | `index.html` + `app.css` + 칸별 `ui_*.js` (README 참고) |

`app_*.py` 맨 위에서는 numpy·shapely 같은 무거운 라이브러리를 불러오지 않습니다(화면이 바로 켜지게). 쓰는 함수 안에서 불러옵니다.
