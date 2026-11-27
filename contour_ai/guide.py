"""결과 폴더에 넣는 '사용_안내.txt': 파일마다 어느 프로그램에서 어떻게 여는지, 단위·축척 (v2.2).

결과 zip 을 받은 사람이 단위를 잘못 읽어 모델이 1000배로 커지거나 작아지는 일을 막는다.
"""
import os


def write_guide(out_dir, summary, scale=1000, interval=5.0):
    """out_dir 에 있는 파일만 골라 안내를 쓴다. 쓴 파일 경로를 돌려준다."""
    has = lambda n: os.path.exists(os.path.join(out_dir, n))  # noqa: E731
    sc = int(scale or 1000)
    iv = float(interval or 5)
    t = iv * 1000 / sc
    L = ["콘타 모델 사용 안내", "=" * 20, "",
         f"축척 1:{sc} · 등고 간격(판 한 장 높이) {iv:g}m = 모형 판 두께 {t:.1f}mm", ""]
    if summary.get("sheets"):
        L.append(f"판 {summary['sheets']}장 · 건물 {summary.get('buildings', 0)}채")
        L.append("")
    if summary.get("osm"):
        L += ["지하철 출입구 번호·선로·역 일부는 OpenStreetMap 자료입니다: © OpenStreetMap contributors (ODbL).",
              "이 모델을 발표·게시할 때는 이 출처를 함께 적어 주세요.", ""]
    L += ["[파일별 단위와 여는 법]", ""]
    if has("model.3dm"):
        L += ["■ model.3dm  (Rhino 7 이상)",
              "  단위: m (실제 크기). 원래 좌표(TM)는 summary.json 의 bbox 에 있습니다.",
              "  레이어: 지형 / 길 / 건물::05층 처럼 층수별 / 나무 / 가로등 / 대지.",
              "  재료로 고르려면 SelMaterialName, 용도로 고르려면 그룹 '용도: 주택' 같은 이름을 쓰세요.", ""]
    if has("laser"):
        L += ["■ laser 폴더의 sheet_00_EL35.dxf …  (레이저 커터, AutoCAD, Illustrator)",
              f"  단위: mm (모형 크기 그대로, 1:{sc}). 크기를 바꾸지 말고 그대로 자르세요.",
              "  판 하나에 한 파일. 파일 이름의 EL 숫자가 그 판의 높이(m)입니다.",
              "  레이어: CUT·CUT_ROAD = 자르는 선, ENGRAVE = 새기는 선(윗판 자리·길), TREE = 나무 구멍, LABEL = 글자.", ""]
    if has("plan_2d.dxf"):
        L += ["■ plan_2d.dxf  (AutoCAD, Rhino, Illustrator) 2D 지형도",
              "  단위: m, 실제 좌표(TM). 수치지도·다른 도면과 그대로 겹쳐집니다.",
              "  레이어: 등고선_주곡선 / 등고선_계곡선(다섯 번째마다) / 등고선_표고 / 건물_05층 처럼 층수별 / 건물_층수 /",
              "          도로_차도·보도 … / 도로_경계선 / 물 / 녹지_숲·조경 / 지적 / 나무 / 가로등 / 대지경계 / 범위 / 항공사진.",
              "  등고선은 높이(Z, 고도)가 들어 있습니다. 항공사진은 plan_2d_항공사진.jpg 를 같은 폴더에 두어야 보입니다.", ""]
    if has("site_diagram.svg"):
        L += ["■ site_diagram.svg  (Illustrator, Figma) 사이트 다이어그램",
              f"  단위: mm, 모형 축척 1:{sc} 크기. 주제별 레이어(등고선·도로·건물·녹지·물·지적·나무·대지경계)로 나뉩니다.",
              "  항공사진 레이어는 숨겨져 있으니 필요하면 켜세요. 건물은 높을수록 진한 회색입니다.", ""]
    if has("section_data.npz"):
        L += ["■ section_NN.svg / section_NN.dxf  지형 단면 (모델을 만든 뒤 화면 지도 탭 '단면선 긋기'로 만들면 생김)",
              f"  SVG 는 mm, 모형 축척 1:{sc}, 높이 과장 없음. DXF 는 x = A 에서 거리(m), y = 높이(EL, m).",
              "  잘린 지형·건물은 진하게, 선 왼쪽 '뒤쪽 깊이' 안의 지형·건물은 연하게 그려집니다.",
              "  section_data.npz 는 단면을 만들 때 쓰는 자료라 지우지 마세요.", ""]
    if has("terrain_photo.obj"):
        L += ["■ terrain_photo.obj / terrain_photo.glb  (Blender, KeyShot, Twinmotion, Lumion, Rhino) 항공사진 지형",
              "  단위: m (실제 크기), 모델 왼쪽 아래가 0,0. 항공사진을 입힌 지형 면입니다.",
              "  OBJ 는 terrain_photo.mtl · terrain_photo.jpg 와 같은 폴더에 두어야 사진이 보입니다.",
              "  OBJ 는 Y 가 위(보통 OBJ 방식)라 Blender 등은 그대로 열립니다. Rhino 에서 누워 보이면 가져오기 옵션에서 Y·Z 바꾸기를 켜세요.", ""]
    if has("model.stl"):
        L += ["■ model.stl  (3D 프린트 슬라이서: Bambu Studio, PrusaSlicer, Cura …)",
              f"  단위: mm (모형 크기 그대로, 1:{sc}). 불러올 때 mm 로 두면 100% 그대로 뽑으면 됩니다.",
              "  지형과 건물이 한 덩어리로 닫힌 솔리드입니다.", ""]
    if has("model_sketchup.dae"):
        L += ["■ model_sketchup.dae  (SketchUp 2015 이상)",
              "  단위: m. 자세한 방법은 '스케치업에서_여는_법.txt' 를 보세요.", ""]
    if has("model.glb"):
        L += ["■ model.glb  (Blender, Twinmotion, Lumion, Unreal, 웹 3D 보기)",
              "  단위: m. Blender: 파일 > 가져오기 > glTF 2.0 (별도 확장 필요 없음).", ""]
    if has("preview_plan.png"):
        L += ["■ preview_plan.png  평면 확인 그림 (판 경계·길·건물)", ""]
    L += ["[주의]",
          "  수치지도(국토지리정보원)를 바탕으로 만든 설계·모형용 자료입니다. 측량 자료가 아닙니다.",
          "  건물 층수가 수치지도에 없으면 추정값입니다 (Rhino 파일을 만들었으면 개체 속성 '층수출처'에서 확인).", ""]
    p = os.path.join(out_dir, "사용_안내.txt")
    with open(p, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write("\n".join(L))
    return p
