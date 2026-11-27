"""스케치업용 출력.

.skp 는 스케치업 프로그램(SDK) 없이는 직접 쓸 수 없어서, 스케치업이 그대로 읽는 COLLADA(.dae)로
레이어 구조를 담아 쓰고, 함께 주는 스케치업 확장(.rbz)이 그것을 불러와 태그·건물 정보를 붙인 뒤
.skp 로 저장한다.

- model_sketchup.dae       : 최상위 그룹 = Rhino 레이어 경로(예: '건물::주택'), 그 안에 건물별 그룹.
                             나무는 수관·줄기 컴포넌트 하나씩을 반복 배치(파일이 가볍다).
- model_sketchup_info.json : 건물 그룹 이름(B0001 …) → 수치지도 속성(명칭·용도·종류·층수 …)
- 스케치업_확장_설치파일(모델아님).rbz : 스케치업 확장. 확장 › 콘타 모델 불러오기 → .dae 선택 → 같은 폴더에 .skp 저장
"""
# 입구: 코드는 기능별 파일(skp_*.py)에 있다. 예전처럼 skp_export 에서 불러도 동작하도록 이름만 다시 내보낸다.
# 여기에 코드를 쓰지 말 것 (CLAUDE.md).
from .skp_dae import (_ncname, _f, _clean, Dae, _ellipsoid_unit, _matrix, SkpJson)  # noqa: F401
from .skp_write import (write_sketchup)  # noqa: F401
from .skp_ruby import (RBZ_NAME, HOWTO, LOADER_RB, MAIN_RB, write_rbz, BUILDER_RB)  # noqa: F401
from .skp_app import (find_sketchup, make_skp_with_sketchup)  # noqa: F401
