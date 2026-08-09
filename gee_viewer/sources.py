"""よく使うデータセットの定義(パイプライン第1段)。

ここに書くのは「役割 → 実バンド名」の対応が確定しているものだけ。
ここに無いデータセットは stac.py 経由で STAC から自動生成できるので、
このファイルは網羅リストではなく「すぐ出てくるショートカット」に近い。

役割名と scale/offset は GEE の STAC メタデータ(center_wavelength /
gee:scale / gee:offset)と突き合わせて確認済み。光学センサは全て反射率
0-1 に正規化されるので、下流の指数と可視化パラメータを共有できる。
"""

from __future__ import annotations

import ee

from . import stac
from .pipeline import Band, Filter, ParamSpec, Source

# ------------------------------------------------------------ フィルタ部品


def cloud_filter(prop: str, default: int = 70) -> Filter:
    """メタデータの雲量プロパティでシーンを絞り込む。"""
    return Filter(
        param=ParamSpec(
            "max_cloud", "雲量上限 (%)", "int", default, 0, 100,
            help=f"シーン単位の絞り込み({prop})。ピクセル単位の除去は雲マスク。",
        ),
        apply=lambda col, v: col.filter(ee.Filter.lt(prop, v)),
        code=lambda v: f"ee.Filter.lt({prop!r}, {v})",
    )


def s1_prefilter(col):
    """Sentinel-1 を IW モードかつ VV/VH 両偏波を持つシーンに揃える。

    偏波の組み合わせはシーンごとに異なり、VV しか無いシーンが混ざると
    VH バンドの select が失敗する。陸域の標準である IW/VV+VH に固定して
    役割マップ(vv, vh)が常に成立するようにしている。
    """
    return (
        col.filter(ee.Filter.eq("instrumentMode", "IW"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
    )


ORBIT_FILTER = Filter(
    param=ParamSpec(
        "orbit", "軌道方向", "select", "DESCENDING",
        options=("DESCENDING", "ASCENDING", "両方"),
        help="SAR は軌道方向で見え方が変わるため、混ぜずに揃えるのが基本。",
    ),
    apply=lambda col, v: (
        col if v == "両方" else col.filter(ee.Filter.eq("orbitProperties_pass", v))
    ),
    code=lambda v: (
        None if v == "両方" else f"ee.Filter.eq('orbitProperties_pass', {v!r})"
    ),
)


# ---------------------------------------------------------------- 雲マスク


def s2_cloud_mask(img):
    """Sentinel-2 の SCL バンドで雲・雲影・巻雲を除去。"""
    scl = img.select("SCL")
    bad = scl.eq(3).Or(scl.gte(8).And(scl.lte(11)))  # 3=雲影, 8/9=雲, 10=巻雲, 11=雪
    return img.updateMask(bad.Not())


def landsat_cloud_mask(img):
    """Landsat Collection 2 の QA_PIXEL ビットで雲・雲影を除去。"""
    qa = img.select("QA_PIXEL")
    clear = (
        qa.bitwiseAnd(1 << 1).eq(0)  # 膨張させた雲
        .And(qa.bitwiseAnd(1 << 3).eq(0))  # 雲
        .And(qa.bitwiseAnd(1 << 4).eq(0))  # 雲影
    )
    return img.updateMask(clear)


def modis_cloud_mask(img):
    """MODIS の state_1km 下位2ビット(雲状態)が 0 = clear のみ残す。"""
    return img.updateMask(img.select("state_1km").bitwiseAnd(3).eq(0))


# ------------------------------------------------------------ 定義ヘルパ


def _optical(names: dict[str, str], scale: float, offset: float = 0.0):
    """光学センサの役割マップ。全バンド同一の scale/offset を適用する。"""
    return {role: Band(band, scale, offset) for role, band in names.items()}


_S2_BANDS = {
    "coastal": "B1", "blue": "B2", "green": "B3", "red": "B4",
    "rededge1": "B5", "rededge2": "B6", "rededge3": "B7",
    "nir": "B8", "nir08": "B8A", "watervapor": "B9",
    "swir1": "B11", "swir2": "B12",
}

_L89_BANDS = {
    "coastal": "SR_B1", "blue": "SR_B2", "green": "SR_B3", "red": "SR_B4",
    "nir": "SR_B5", "swir1": "SR_B6", "swir2": "SR_B7",
}

_L457_BANDS = {
    "blue": "SR_B1", "green": "SR_B2", "red": "SR_B3",
    "nir": "SR_B4", "swir1": "SR_B5", "swir2": "SR_B7",
}

_MODIS_BANDS = {
    "red": "sur_refl_b01", "nir": "sur_refl_b02", "blue": "sur_refl_b03",
    "green": "sur_refl_b04", "swir1": "sur_refl_b06", "swir2": "sur_refl_b07",
}

# Landsat C2 L2 の反射率スケーリング(全世代共通)
_L_SCALE, _L_OFFSET = 2.75e-05, -0.2
# 地表面温度はケルビン。反射率と混ぜないよう役割を分ける。
_LST = Band("ST_B10", 0.00341802, 149.0)


def _landsat(sid: str, label: str, asset: str, bands: dict, lst: Band | None):
    roles = _optical(bands, _L_SCALE, _L_OFFSET)
    if lst is not None:
        roles["thermal"] = lst
    return Source(
        id=sid, label=label, asset=asset, kind="optical", roles=roles,
        filters=(cloud_filter("CLOUD_COVER"),),
        cloud_mask=landsat_cloud_mask, native_scale=30,
        value_range=(0.0, 0.3),
        description="Collection 2 地表面反射率。反射率 0-1 に正規化済み。",
        category="光学衛星",
    )


# -------------------------------------------------------------- カタログ

CURATED: tuple[Source, ...] = (
    Source(
        id="s2_sr",
        label="Sentinel-2 SR (10m, 2017-)",
        asset="COPERNICUS/S2_SR_HARMONIZED",
        kind="optical",
        roles=_optical(_S2_BANDS, 1e-4),
        filters=(cloud_filter("CLOUDY_PIXEL_PERCENTAGE"),),
        cloud_mask=s2_cloud_mask,
        native_scale=10,
        value_range=(0.0, 0.3),
        description="最も使い勝手の良い光学衛星。10m・5日周期・レッドエッジ4バンド。",
        category="光学衛星",
    ),
    Source(
        id="s2_toa",
        label="Sentinel-2 TOA (10m, 2015-)",
        asset="COPERNICUS/S2_HARMONIZED",
        kind="optical",
        roles=_optical(_S2_BANDS, 1e-4),
        filters=(cloud_filter("CLOUDY_PIXEL_PERCENTAGE"),),
        native_scale=10,
        value_range=(0.0, 0.3),
        description="大気上端反射率。2017年以前を見たい場合はこちら(SRは2017年開始)。",
        category="光学衛星",
    ),
    _landsat("l9", "Landsat 9 (30m, 2021-)", "LANDSAT/LC09/C02/T1_L2", _L89_BANDS, _LST),
    _landsat("l8", "Landsat 8 (30m, 2013-)", "LANDSAT/LC08/C02/T1_L2", _L89_BANDS, _LST),
    _landsat("l7", "Landsat 7 (30m, 1999-)", "LANDSAT/LE07/C02/T1_L2", _L457_BANDS,
             Band("ST_B6", 0.00341802, 149.0)),
    _landsat("l5", "Landsat 5 (30m, 1984-2012)", "LANDSAT/LT05/C02/T1_L2", _L457_BANDS,
             Band("ST_B6", 0.00341802, 149.0)),
    Source(
        id="modis",
        label="MODIS 日次反射率 (500m, 2000-)",
        asset="MODIS/061/MOD09GA",
        kind="optical",
        roles=_optical(_MODIS_BANDS, 1e-4),
        cloud_mask=modis_cloud_mask,
        native_scale=500,
        value_range=(0.0, 0.3),
        description="毎日全球を撮る。広域・長期の変化を粗い解像度で見るとき向け。",
        category="光学衛星",
    ),
    Source(
        id="naip",
        label="NAIP 空中写真 (1m, 米国のみ)",
        asset="USDA/NAIP/DOQQ",
        kind="optical",
        roles={"red": Band("R"), "green": Band("G"), "blue": Band("B"), "nir": Band("N")},
        native_scale=1,
        value_range=(0.0, 255.0),
        description="1m解像度の空中写真。米国本土のみ。値は 0-255 の DN。",
        category="光学衛星",
    ),
    Source(
        id="s1_grd",
        label="Sentinel-1 SAR (10m, 2014-)",
        asset="COPERNICUS/S1_GRD",
        kind="sar",
        roles={"vv": Band("VV"), "vh": Band("VH")},
        filters=(ORBIT_FILTER,),
        prefilter=s1_prefilter,
        native_scale=10,
        value_range=(-25.0, 0.0),
        description="レーダー。雲を透過し夜間も撮れる。値は後方散乱係数 dB。水面は暗い。",
        category="SAR",
    ),
    Source(
        id="palsar",
        label="ALOS PALSAR 年次モザイク (25m)",
        asset="JAXA/ALOS/PALSAR/YEARLY/SAR_EPOCH",
        kind="sar",
        roles={"hh": Band("HH"), "hv": Band("HV")},
        native_scale=25,
        value_range=(0.0, 8000.0),
        description="JAXA の L バンド SAR 年次モザイク。植生の透過性が高い。",
        category="SAR",
    ),
    Source(
        id="dem_glo30",
        label="Copernicus DEM GLO-30 (30m)",
        asset="COPERNICUS/DEM/GLO30",
        kind="dem",
        roles={"elevation": Band("DEM")},
        temporal=False,
        native_scale=30,
        value_range=(0.0, 1500.0),
        description="全球標高モデル。期間指定は無視される。",
        category="地形",
    ),
    Source(
        id="srtm",
        label="SRTM 標高 (30m)",
        asset="USGS/SRTMGL1_003",
        asset_type="image",
        kind="dem",
        roles={"elevation": Band("elevation")},
        temporal=False,
        native_scale=30,
        value_range=(0.0, 1500.0),
        description="定番の全球標高モデル(2000年観測)。",
        category="地形",
    ),
    Source(
        id="aw3d30",
        label="ALOS AW3D30 標高 (30m)",
        asset="JAXA/ALOS/AW3D30/V3_2",
        kind="dem",
        roles={"elevation": Band("DSM")},
        temporal=False,
        native_scale=30,
        value_range=(0.0, 1500.0),
        description="JAXA の全球数値表層モデル。日本域の品質が高い。",
        category="地形",
    ),
    Source(
        id="worldcover",
        label="ESA WorldCover 土地被覆 (10m, 2021)",
        asset="ESA/WorldCover/v200",
        kind="landcover",
        roles={"landcover": Band("Map")},
        temporal=False,
        native_scale=10,
        value_range=(10.0, 100.0),
        description="10m解像度の全球土地被覆分類(11クラス)。",
        category="土地被覆",
    ),
    Source(
        id="dynamic_world",
        label="Dynamic World 土地被覆確率 (10m, 2015-)",
        asset="GOOGLE/DYNAMICWORLD/V1",
        kind="landcover",
        roles={
            r: Band(r)
            for r in (
                "water", "trees", "grass", "flooded_vegetation", "crops",
                "shrub_and_scrub", "built", "bare", "snow_and_ice",
            )
        },
        native_scale=10,
        value_range=(0.0, 1.0),
        description="Sentinel-2 から推論した9クラスの土地被覆確率。時系列で見られる。",
        category="土地被覆",
    ),
    Source(
        id="viirs_ntl",
        label="VIIRS 夜間光 (月次, 500m)",
        asset="NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG",
        kind="other",
        roles={"radiance": Band("avg_rad")},
        native_scale=500,
        value_range=(0.0, 60.0),
        description="夜間光の月次平均放射輝度。都市の広がりや経済活動の代理指標。",
        category="その他",
    ),
    Source(
        id="s5p_no2",
        label="Sentinel-5P NO2 (7km)",
        asset="COPERNICUS/S5P/OFFL/L3_NO2",
        kind="other",
        roles={"no2": Band("tropospheric_NO2_column_number_density")},
        native_scale=7000,
        value_range=(0.0, 0.0002),
        description="対流圏 NO2 カラム量。大気汚染の分布。",
        category="その他",
    ),
)

BY_ID: dict[str, Source] = {s.id: s for s in CURATED}

CATEGORIES: tuple[str, ...] = tuple(
    dict.fromkeys(s.category for s in CURATED)
)


def in_category(category: str) -> list[Source]:
    return [s for s in CURATED if s.category == category]


def resolve(source_id: str) -> Source:
    """ID から Source を解決する。

    "stac:" 接頭辞が付いていれば STAC から動的に組み立てる。これにより
    パイプライン定義を「ただのデータ」に保てる(キャッシュキーにできる)。
    """
    if source_id.startswith("stac:"):
        return stac.source_from_stac(source_id[len("stac:"):])
    return BY_ID[source_id]
