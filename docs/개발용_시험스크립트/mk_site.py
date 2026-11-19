import ezdxf, sys
X0, Y0 = 201300.0, 554600.0
pts = [(316, 316), (371, 316), (371, 372), (352, 398), (316, 398)]
doc = ezdxf.new("R2010"); msp = doc.modelspace()
msp.add_lwpolyline([(X0 + x, Y0 + y) for x, y in pts], close=True, dxfattribs={"layer": "대지경계"})
doc.saveas(sys.argv[1])
