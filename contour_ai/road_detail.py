"""도로 디테일: 노면표시(차선·중앙선·정지선·횡단보도), 신호등, 전봇대, 주차 구획.

인도는 이미 수치지도 보도 면(N3A_A0033320)을 연석 높이만큼 올려 만든다.

출처 (앞쪽이 우선)
- 노면표시
  1. 항공사진(브이월드 영상·정사영상)이 있으면 차도 안의 흰색·노란색 페인트를 찾아 그대로 면으로 (레이어 '...::항공사진')
  2. 횡단보도 자료 파일 (전국횡단보도표준데이터: 위도·경도·횡단보도폭·연장)
  3. 추정: 수치지도 도로중심선(N3L_A0020000)의 차로수·분리대유무로 중앙선(노랑)·차선(흰 점선),
     차로 2개 이상 길끼리 만나는 교차로마다 횡단보도와 정지선
- 신호등
  1. 신호등 자료 파일 (전국신호등표준데이터: 위도·경도·신호등구분)
  2. 추정: 횡단보도 양 끝에 보행 신호등, 교차로 진입로마다 건너편 오른쪽 모서리에 차량 신호등(팔 달린 기둥)
- 전봇대
  1. 위치 자료 파일 (위도·경도 칸, 파일 이름이나 칸에 '전주'·'전봇대')
  2. 추정: 폭 12m 미만 생활도로에만 한쪽 30m 간격 (큰길은 서울 대부분 지중화) — 보안등 5m 안에서는 생략
     한전 전주 좌표는 공개 자료가 없다(전주번호만 공개).
- 주차장: 수치지도 주차장 면(N3A_C0430000) 안에 2.5×5.0m 구획 + 6m 통로 줄을 긴 축 방향으로 깐다(추정).

반환은 2D 도형과 점이고, 높이는 contour_model 이 붙인다.

이 파일은 입구입니다. 실제 코드는 roads/ 폴더에 기능별로 나뉘어 있습니다.
"""
from .roads.style import (  # noqa: F401
    FIX_RGB,
    LANE_W,
    PAINT,
)
from .roads.data_files import (  # noqa: F401
    _dedupe,
    _f,
    _kind_of_table,
    read_files,
)
from .roads.centerlines import (  # noqa: F401
    ISLANDS,
    _all_walk,
    _approach_dir,
    _carriage,
    _centerlines,
    _crossing,
    _is_island,
    _nodes,
    _offset_strips,
    _span,
    _split_dual,
    _strip,
)
from .roads.junctions import (  # noqa: F401
    _mouth,
    _road_dir_at,
    chain_links,
    dedupe_bands,
    junction_cores,
    snap_crossings,
)
from .roads.islands import (  # noqa: F401
    crosswalk_islands,
    hd_islands,
    islands_at_points,
    islands_from_aerial,
)
from .roads.lanes import (  # noqa: F401
    _arm_end,
    _curb_pair,
    _ransac_line,
    _section,
    _stop_and_arrows,
    _zebra,
    arm_frame,
    arm_lanes,
    estimate_marks,
    real_crosswalks,
)
from .roads.aerial import (  # noqa: F401
    aerial_marks,
    aerial_zebras,
)
from .roads.street_furniture import (  # noqa: F401
    estimate_poles,
    estimate_signals,
)
from .roads.parking import (  # noqa: F401
    parking_lots,
    parking_marks,
)
from .roads.shapes import (  # noqa: F401
    flat_mesh,
    placed,
    pole_parts,
    signal_parts,
)
from .roads.assemble import (  # noqa: F401
    CW_RANK,
    _apply_hdmap,
    road_details,
    tidy_crosswalks,
)
from .roads.check_image import (  # noqa: F401
    SRC_COLOR,
    marks_check,
)
