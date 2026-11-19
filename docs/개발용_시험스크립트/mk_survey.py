import ezdxf, sys
doc = ezdxf.new("R2010"); m = doc.modelspace()
X = [213960, 213995, 213997, 214028, 214030, 214060]; Y0, Y1 = 515830, 515925
bnd = [(213960, 515830), (214060, 515830), (214060, 515925), (213960, 515925)]
m.add_lwpolyline(bnd, close=True, dxfattribs={"layer": "대지경계"})
if sys.argv[2] == "1":
    pads = [(X[0], X[1], 178.0), (X[2], X[3], 170.0), (X[4], X[5], 162.0)]
    for x0, x1, z in pads:
        # 단 테두리 = 그 높이의 등고선
        m.add_lwpolyline([(x0, Y0), (x1, Y0), (x1, Y1), (x0, Y1)], close=True,
                         dxfattribs={"layer": "LEVEL", "elevation": z})
        m.add_text(f"EL+{z:.2f}", dxfattribs={"layer": "TEXT", "height": 2}).set_placement(((x0 + x1) / 2, (Y0 + Y1) / 2))
        for y in (Y0 + 15, Y1 - 15):
            m.add_text(f"FL {z:.2f}", dxfattribs={"layer": "TEXT", "height": 1}).set_placement(((x0 + x1) / 2, y))
    m.add_text("A=9,500.00㎡", dxfattribs={"layer": "TEXT", "height": 2}).set_placement((214010, 515935))
    m.add_text("3.50", dxfattribs={"layer": "DIM", "height": 1}).set_placement((213990, 515928))
doc.saveas(sys.argv[1])
