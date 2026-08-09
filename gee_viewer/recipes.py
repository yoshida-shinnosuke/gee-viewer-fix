"""バンド合成・指数(パイプライン第2段)。

Source が済ませたバンド名の正規化のおかげで、ここは役割名だけで書ける。
NDVI の定義は1つしか無いが、red と nir を持つデータセットなら
Sentinel-2 でも Landsat でも MODIS でも同じ定義がそのまま動く。

requires に挙げた役割を持たない Source では、その Recipe は UI に現れない。

## build と code の対

各 Recipe は実行用の `build`(ee オブジェクトを組む)と、書き出し用の
`code`(同じ処理のソースコード文字列)を持つ。両者がズレると
「画面に出ている絵」と「書き出したコード」が食い違うため、
定型的なものは **同じ引数から両方を生成するファクトリ**で定義している。
"""

from __future__ import annotations

import ee

from .pipeline import ParamSpec, Recipe, Source

# 指数の共通可視化(-1〜1)
_INDEX_VIS = {"min": -1, "max": 1, "palette": ["#8c510a", "#f5f5f5", "#01665e"]}
_VEG_VIS = {"min": -1, "max": 1, "palette": ["#0000ff", "#ffffff", "#006400"]}
_WATER_VIS = {"min": -1, "max": 1, "palette": ["#8b4513", "#ffffff", "#0000cd"]}


# ------------------------------------------------------------- ファクトリ


def _composite(rid: str, label: str, roles: tuple[str, ...], description: str) -> Recipe:
    """役割を RGB の順に並べた合成画像。build と code を同じ roles から作る。"""
    bands = list(roles)
    return Recipe(
        id=rid,
        label=label,
        build=lambda img, p, src: img.select(bands),
        code=lambda p, src: f"image = image.select({bands!r})",
        requires=frozenset(roles),
        produces="rgb",
        description=description,
    )


def _index(rid: str, label: str, a: str, b: str, name: str,
           vis: dict, description: str) -> Recipe:
    """(a − b) / (a + b) 型の正規化指数。build と code を同じ a, b から作る。"""
    pair = [a, b]
    return Recipe(
        id=rid,
        label=label,
        build=lambda img, p, src: img.normalizedDifference(pair).rename(name),
        code=lambda p, src: (
            f"image = image.normalizedDifference({pair!r}).rename({name!r})"
        ),
        requires=frozenset(pair),
        vis=vis,
        description=description,
    )


def _band(rid: str, label: str, role: str, vis: dict | None,
          description: str) -> Recipe:
    """単一の役割バンドをそのまま取り出す。"""
    return Recipe(
        id=rid,
        label=label,
        build=lambda img, p, src: img.select([role]),
        code=lambda p, src: f"image = image.select([{role!r}])",
        requires=frozenset({role}),
        vis=vis,
        description=description,
    )


# ------------------------------------------------------------ 個別の定義


def _single_band_role(params: dict, src: Source) -> str:
    return params.get("band") or next(iter(src.roles))


def _evi(img, params, src):
    """EVI。大気とキャノピー背景の影響を NDVI より抑えた植生指数。"""
    return img.expression(
        "2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1)",
        {"nir": img.select("nir"), "red": img.select("red"), "blue": img.select("blue")},
    ).rename("EVI")


_EVI_CODE = """image = image.expression(
    '2.5 * (nir - red) / (nir + 6 * red - 7.5 * blue + 1)',
    {'nir': image.select('nir'),
     'red': image.select('red'),
     'blue': image.select('blue')},
).rename('EVI')"""


def _savi(img, params, src):
    """SAVI。土壌の影響を補正した植生指数(L は裸地率に応じた補正係数)。"""
    L = params.get("L", 0.5)
    return img.expression(
        "((nir - red) / (nir + red + L)) * (1 + L)",
        {"nir": img.select("nir"), "red": img.select("red"), "L": L},
    ).rename("SAVI")


def _savi_code(params, src):
    return f"""image = image.expression(
    '((nir - red) / (nir + red + L)) * (1 + L)',
    {{'nir': image.select('nir'),
     'red': image.select('red'),
     'L': {params.get('L', 0.5)!r}}},
).rename('SAVI')"""


def _sar_ratio(img, params, src):
    return img.select("vv").subtract(img.select("vh")).rename("VV-VH")


def _sar_composite(img, params, src):
    """VV / VH / VV−VH を RGB に割り当てた擬似カラー。地表被覆の差が出る。"""
    return ee.Image.cat(
        img.select("vv"),
        img.select("vh"),
        img.select("vv").subtract(img.select("vh")),
    ).rename(["R", "G", "B"])


_SAR_COMPOSITE_CODE = """image = ee.Image.cat(
    image.select('vv'),
    image.select('vh'),
    image.select('vv').subtract(image.select('vh')),
).rename(['R', 'G', 'B'])"""


# 並び順がそのまま UI の並び順・既定値になる。requires を満たす最初の
# エントリが既定になるため、汎用だが絵として分かりにくい raw / single は
# 末尾に置く(光学ならトゥルーカラー、SAR なら VV が既定になる)。
RECIPES: tuple[Recipe, ...] = (
    # ------------------------------------------------------ 光学カラー合成
    _composite("true_color", "トゥルーカラー", ("red", "green", "blue"),
               "肉眼に近い自然な色。"),
    _composite("false_color", "フォールスカラー (植生強調)", ("nir", "red", "green"),
               "NIR/Red/Green。植生が赤く見える。"),
    _composite("swir_color", "SWIR合成 (地質・焼失地)", ("swir2", "nir", "red"),
               "SWIR2/NIR/Red。焼失地・裸地・岩石の判別に強い。"),
    _composite("agriculture", "農地合成", ("swir1", "nir", "blue"),
               "SWIR1/NIR/Blue。作物の活性度の差が出やすい。"),
    # ------------------------------------------------------------- 指数
    _index("ndvi", "NDVI (植生指数)", "nir", "red", "NDVI", _VEG_VIS,
           "(NIR−Red)/(NIR+Red)。緑が濃いほど植生が活発。"),
    _index("ndwi", "NDWI (水域指数)", "green", "nir", "NDWI", _WATER_VIS,
           "(Green−NIR)/(Green+NIR)。青いほど水域。海岸線抽出の元データ。"),
    _index("mndwi", "MNDWI (改良水域指数)", "green", "swir1", "MNDWI", _WATER_VIS,
           "SWIR を使う水域指数。市街地を水と誤判定しにくく NDWI より安定。"),
    _index("ndbi", "NDBI (市街地指数)", "swir1", "nir", "NDBI", _INDEX_VIS,
           "(SWIR1−NIR)/(SWIR1+NIR)。建物・舗装面で高くなる。"),
    _index("nbr", "NBR (焼失指数)", "nir", "swir2", "NBR", _INDEX_VIS,
           "(NIR−SWIR2)/(NIR+SWIR2)。山火事の焼失域評価に使う。"),
    _index("ndsi", "NDSI (積雪指数)", "green", "swir1", "NDSI",
           {"min": -1, "max": 1, "palette": ["#404040", "#ffffff", "#00bfff"]},
           "(Green−SWIR1)/(Green+SWIR1)。積雪域で高くなる。"),
    Recipe(
        id="evi",
        label="EVI (改良植生指数)",
        build=_evi,
        code=lambda p, src: _EVI_CODE,
        requires=frozenset({"nir", "red", "blue"}),
        vis=_VEG_VIS,
        description="NDVI が飽和しやすい密な植生でも差が出る。",
    ),
    Recipe(
        id="savi",
        label="SAVI (土壌調整植生指数)",
        build=_savi,
        code=_savi_code,
        requires=frozenset({"nir", "red"}),
        vis=_VEG_VIS,
        params=(
            ParamSpec("L", "土壌補正係数 L", "float", 0.5, 0.0, 1.0, 0.1,
                      help="植被が少ないほど大きく。0 だと NDVI と一致する。"),
        ),
        description="裸地が多い場所で NDVI より安定する植生指数。",
    ),
    # -------------------------------------------------------------- SAR
    _band("sar_vv", "SAR VV (dB)", "vv", None,
          "同一偏波。水面が暗く、都市が明るい。"),
    _band("sar_vh", "SAR VH (dB)", "vh", None,
          "交差偏波。体積散乱が強い植生で明るい。"),
    Recipe(
        id="sar_ratio",
        label="SAR VV−VH (偏波比)",
        build=_sar_ratio,
        code=lambda p, src: (
            "image = image.select('vv').subtract(image.select('vh'))"
            ".rename('VV-VH')"
        ),
        requires=frozenset({"vv", "vh"}),
        vis={"min": 0, "max": 15},
        description="dB の差 = 比。散乱機構の違いを強調する。",
    ),
    Recipe(
        id="sar_composite",
        label="SAR 擬似カラー (VV/VH/比)",
        build=_sar_composite,
        code=lambda p, src: _SAR_COMPOSITE_CODE,
        requires=frozenset({"vv", "vh"}),
        produces="rgb",
        vis={"min": [-25, -30, 0], "max": [0, -5, 15]},
        description="VV・VH・VV−VH を RGB に割り当てた合成。土地被覆の差が見える。",
    ),
    # ------------------------------------------------------------- 地形
    _band("elevation", "標高", "elevation",
          {"min": 0, "max": 1500,
           "palette": ["#006633", "#e5ffcc", "#ffcc66", "#993300", "#ffffff"]},
          "標高 (m)。"),
    # ----------------------------------------------------- 生バンド(汎用)
    Recipe(
        id="single",
        label="単バンド表示",
        build=lambda img, p, src: img.select([_single_band_role(p, src)]),
        code=lambda p, src: f"image = image.select([{_single_band_role(p, src)!r}])",
        produces="single",
        params=(
            ParamSpec(
                "band", "バンド", "select", None,
                options_from_source=lambda src: tuple(src.roles),
                help="このデータセットが持つバンド(役割名)から1つ選ぶ。",
            ),
        ),
        description="任意の1バンドをグレースケール表示。どのデータセットでも使える。",
    ),
    Recipe(
        id="raw",
        label="全バンドそのまま",
        build=lambda img, p, src: img,
        code=lambda p, src: "# 正規化済みの全バンドをそのまま使う",
        produces="multi",
        description="正規化済みの全バンドを渡す。クラスタリングなど多変量解析の入力用。",
    ),
)

BY_ID: dict[str, Recipe] = {r.id: r for r in RECIPES}
