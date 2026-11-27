# 버스정류장·지하철 출입구 확인 그림 만들기 (v2.4.1)

1. `python mk.py` : 가짜 도엽에 무벽건물(보도 정류장 쉘터·지하철 출입구 캐노피·중앙버스정류장 지붕)과 정류소 위치 CSV 를 더함
2. `python run.py <결과폴더>` : 가짜 OpenStreetMap 응답(출입구 5곳, 그중 1곳은 건물 안 + 역 + 선로)으로 300m 모델
3. `r.html`(three.js) 을 결과 GLB 옆에 두고 `node shoot.js views.json` 으로 Playwright 스크린샷

경로는 작업할 때 쓴 것(/home/claude/tt)이라 쓸 때 바꾸세요. 실제 수치지도·OSM 은 쓰지 않습니다.
