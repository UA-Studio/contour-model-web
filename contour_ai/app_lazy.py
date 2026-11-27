"""무거운 모듈(numpy·trimesh·rhino3dm 등)을 처음 쓸 때 불러오는 도구.
"""


class _LazyModule:
    """무거운 모듈(numpy·trimesh·rhino3dm 등)은 처음 쓸 때 불러온다.
    서버가 켜질 때 이것들을 먼저 불러오느라 14초 동안 접속을 못 받던 것(fly 'instance refused connection')을 없앤다."""

    def __init__(self, name):
        self._name, self._mod = name, None

    def __getattr__(self, attr):
        if self._mod is None:
            import importlib
            self._mod = importlib.import_module(self._name, __package__)
        return getattr(self._mod, attr)


pipeline = _LazyModule(".pipeline")
