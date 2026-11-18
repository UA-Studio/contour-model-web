# 공개 서버 (웹 서비스)

윈도우판과 같은 프로그램을 여러 사람이 사이트에서 쓰도록 돌리는 설정입니다.

- 실행: `CONTOUR_SERVER=1 python -m contour_ai serve` (Docker 이미지가 이렇게 실행)
- 첫 화면 `/` 소개, `/app` 프로그램, `/admin/feedback?token=…` 받은 의견
- 배포: `main` 에 올리면 GitHub Actions 가 fly.io 에 배포 (`FLY_API_TOKEN` 비밀값 필요)

## fly.io 비밀값 (`fly secrets set 이름=값`)
| 이름 | 쓰임 |
|---|---|
| `CONTOUR_ADMIN_TOKEN` | 받은 의견 화면 비밀번호 |
| `VWORLD_KEY`, `VWORLD_DOMAIN` | 브이월드 (항공사진·지적·법규 검토) |
| `DATA_GO_KR_KEY` | 건축물대장 |
| `SEOUL_KEY`, `SAFEMAP_KEY` | 횡단보도·보안등 (선택) |

## 제한 (fly.toml `[env]`)
한 변 `CONTOUR_MAX_SIDE`(1000m), 하루 횟수 `CONTOUR_RUNS_PER_DAY`(5), 파일 `CONTOUR_MAX_UPLOAD_MB`(150),
작업 시간 `CONTOUR_JOB_MINUTES`(12), 보관 `CONTOUR_KEEP_HOURS`(24).

서버에서는 정밀도로지도(재배포 금지), PC 경로 입력, 폴더 열기를 쓰지 않습니다.
