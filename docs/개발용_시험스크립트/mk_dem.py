"""시험용 가짜 5m DEM: 가짜 도엽 지형(H) + 동쪽 둔덕에 채석장 계단 절토면(등고선에는 없음)."""
import sys, numpy as np, rasterio
from rasterio.transform import from_origin
X0, Y0, W = 201300.0, 554600.0, 600.0
def H(x, y):
    u, v = (x - X0) / W, (y - Y0) / W
    return 30 + 28 * u * 0.6 + 22 * v + 9 * np.exp(-((u - .7) ** 2 + (v - .65) ** 2) / .03)
c = 5.0
xs = np.arange(X0 - 40, X0 + W + 40, c) + c / 2; ys = np.arange(Y0 + W + 40, Y0 - 40, -c) - c / 2
XX, YY = np.meshgrid(xs, ys); Z = H(XX, YY)
# 채석장: 중심 (X0+380, Y0+430) 둘레 120m 안을 6m 높이 계단(폭 10m)으로 깎음
cx, cy = X0 + 380, Y0 + 430
d = np.hypot(XX - cx, (YY - cy) * 1.3)
cut = d < 120
bench = 44 + 6 * np.floor(np.clip(d, 0, 120) / 12)
Z = np.where(cut, np.minimum(Z, bench), Z)
with rasterio.open(sys.argv[1], "w", driver="GTiff", width=Z.shape[1], height=Z.shape[0], count=1, dtype="float32",
                   crs="EPSG:5186", transform=from_origin(xs[0] - c / 2, ys[0] + c / 2, c, c), nodata=-9999) as ds:
    ds.write(Z.astype("float32"), 1)
print(Z.shape)
