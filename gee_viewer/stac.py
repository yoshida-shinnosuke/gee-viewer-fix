"""GEE STAC カタログからの Source 自動生成。

Earth Engine の全データセットのメタデータは STAC(SpatioTemporal Asset
Catalog)として公開されており、認証なしで取得できる:

    https://storage.googleapis.com/earthengine-stac/catalog/{提供元}/{ID}.json

ここから取れる情報のうち、このアプリが使うのは以下:

    center_wavelength  各バンドの中心波長 → 役割(red/nir/...)を自動推定
    gee:scale/offset   物理量への変換係数 → 反射率 0-1 などに正規化
    gee:visualizations 公式の可視化プリセット → 役割を推定できない場合の保険
    gee:type           image か image_collection か
    gsd                解像度

STAC には STAC 拡張の common_name(blue/red/nir 等)は入っていないので、
役割の割り当ては中心波長の最近傍マッチで行う(_infer_roles)。

これにより、手書きのカタログに無いデータセットでも ID を指定するだけで
パイプラインに載せられる。
"""

from __future__ import annotations

import json
import urllib.request
from functools import lru_cache

import ee

from .pipeline import Band, Filter, ParamSpec, Source

BASE = "https://storage.googleapis.com/earthengine-stac/catalog"
TIMEOUT = 30

# 主要な役割。先に確定させる(下の SECONDARY_WAVELENGTHS より優先)。
CORE_WAVELENGTHS = {
    "coastal": 0.443,
    "blue": 0.490,
    "green": 0.560,
    "red": 0.665,
    "nir": 0.842,
    "swir1": 1.610,
    "swir2": 2.200,
    "thermal": 10.90,
}

# 主要役割が埋まった後、余ったバンドに割り当てる補助的な役割。
SECONDARY_WAVELENGTHS = {
    "rededge1": 0.705,
    "rededge2": 0.740,
    "rededge3": 0.783,
    "nir08": 0.865,
    "watervapor": 0.945,
    "cirrus": 1.375,
}

# 中心波長の許容誤差(中心波長に対する相対値)
TOLERANCE = 0.09

# 雲量を持つコレクションでよく使われるプロパティ名
CLOUD_PROPERTIES = ("CLOUDY_PIXEL_PERCENTAGE", "CLOUD_COVER", "CLOUD_COVERAGE")


# ------------------------------------------------------------------ 取得系


def stac_url(ee_id: str) -> str:
    """EEアセットIDから STAC の JSON URL を組み立てる。

    例: COPERNICUS/S2_SR_HARMONIZED
        → .../catalog/COPERNICUS/COPERNICUS_S2_SR_HARMONIZED.json
    """
    provider = ee_id.split("/")[0]
    return f"{BASE}/{provider}/{ee_id.replace('/', '_')}.json"


@lru_cache(maxsize=512)
def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as r:
        return json.load(r)


def fetch_dataset(ee_id: str) -> dict:
    """データセット1件の STAC メタデータを取得。"""
    return _get_json(stac_url(ee_id))


@lru_cache(maxsize=1)
def list_providers() -> tuple[tuple[str, str], ...]:
    """提供元の一覧を (表示名, カタログキー) で返す。約130件。"""
    root = _get_json(f"{BASE}/catalog.json")
    out = []
    for link in root.get("links", []):
        if link.get("rel") != "child":
            continue
        key = link["href"].rstrip("/").split("/")[-2]
        out.append((link.get("title") or key, key))
    return tuple(sorted(out))


@lru_cache(maxsize=64)
def list_datasets(provider_key: str) -> tuple[tuple[str, str], ...]:
    """提供元配下のデータセットを (表示名, EEアセットID) で返す。

    提供元カタログは入れ子になっておらず、1リクエストで全件取れる。
    """
    cat = _get_json(f"{BASE}/{provider_key}/catalog.json")
    out = []
    for link in cat.get("links", []):
        if link.get("rel") != "child" or link["href"].endswith("catalog.json"):
            continue
        # 一覧のリンクには EE アセットID が入っておらず、"/" を "_" に潰した
        # ファイル名しか無い。機械的な復元はできないので、選択された時点で
        # ee_id_from_stem() が本体を1回引いて正式なIDを取り出す。
        stem = link["href"].rstrip("/").split("/")[-1].removesuffix(".json")
        out.append((link.get("title") or stem, stem))
    return tuple(sorted(out))


# -------------------------------------------------------------- 役割の推定


def _assign(targets: dict, bands: list, roles: dict, used: set) -> None:
    """(役割, バンド)の全候補を距離順に貪欲マッチする。"""
    pairs = []
    for role, center in targets.items():
        if role in roles:
            continue
        for b in bands:
            cw = b.get("center_wavelength")
            if cw is None or b["name"] in used:
                continue
            if abs(cw - center) <= center * TOLERANCE:
                pairs.append((abs(cw - center), role, b["name"]))
    pairs.sort()
    for _, role, name in pairs:
        if role in roles or name in used:
            continue
        roles[role] = name
        used.add(name)


def infer_roles(bands: list) -> dict[str, str]:
    """中心波長から役割 → 実バンド名の対応を推定する。

    2段階にするのが要点。Landsat 8/9 の NIR は 0.865µm で、これは
    Sentinel-2 の B8A(nir08)の中心波長と一致してしまう。1段階の貪欲法だと
    nir08 に取られて nir が空になり、NDVI が使えなくなる。主要役割を先に
    確定させることでこれを防ぐ。
    """
    roles: dict[str, str] = {}
    used: set[str] = set()
    _assign(CORE_WAVELENGTHS, bands, roles, used)
    _assign(SECONDARY_WAVELENGTHS, bands, roles, used)
    return roles


def _bands_with_scaling(stac_bands: list, mapping: dict[str, str]) -> dict[str, Band]:
    info = {b["name"]: b for b in stac_bands}
    out = {}
    for role, name in mapping.items():
        b = info.get(name, {})
        out[role] = Band(
            name=name,
            scale=b.get("gee:scale") or 1.0,
            offset=b.get("gee:offset") or 0.0,
        )
    return out


def _cloud_filter(schema: list) -> tuple[Filter, ...]:
    """コレクションが雲量プロパティを持つならフィルタを1つ用意する。"""
    names = {s.get("name") for s in schema}
    for prop in CLOUD_PROPERTIES:
        if prop in names:
            return (
                Filter(
                    param=ParamSpec(
                        "max_cloud", "雲量上限 (%)", "int", 70, 0, 100,
                        help=f"メタデータ {prop} による絞り込み。",
                    ),
                    apply=lambda col, v, _p=prop: col.filter(ee.Filter.lt(_p, v)),
                    code=lambda v, _p=prop: f"ee.Filter.lt({_p!r}, {v})",
                ),
            )
    return ()


def _value_range(stac: dict, roles: dict[str, Band]) -> tuple[float, float]:
    """公式プリセットの min/max を表示レンジとして流用する。"""
    for v in stac.get("summaries", {}).get("gee:visualizations", []):
        bv = v.get("image_visualization", {}).get("band_vis", {})
        lo, hi = bv.get("min"), bv.get("max")
        if lo is None or hi is None:
            continue
        lo, hi = float(lo[0] if isinstance(lo, list) else lo), float(
            hi[0] if isinstance(hi, list) else hi
        )
        # プリセットは生値ベースなので、正規化した分だけスケールを合わせる
        sample = next(iter(roles.values()), None)
        if sample is not None and sample.scale != 1.0:
            lo = lo * sample.scale + sample.offset
            hi = hi * sample.scale + sample.offset
        return lo, hi
    return 0.0, 1.0


def _time_range(stac: dict) -> tuple[str | None, str | None]:
    """データが存在する期間を (開始, 終了) の YYYY-MM-DD で返す。"""
    intervals = stac.get("extent", {}).get("temporal", {}).get("interval") or []
    if not intervals or not intervals[0]:
        return (None, None)
    start, end = (intervals[0] + [None, None])[:2]
    return (
        start[:10] if isinstance(start, str) else None,
        end[:10] if isinstance(end, str) else None,
    )


def source_from_stac(ee_id: str) -> Source:
    """任意の EE データセットIDから Source を組み立てる。

    波長情報があれば役割を推定し、無ければ実バンド名をそのまま役割として
    扱う(その場合は汎用指数は使えないが、単バンド表示は可能)。
    """
    stac = fetch_dataset(ee_id)
    summaries = stac.get("summaries", {})
    stac_bands = summaries.get("eo:bands", [])

    mapping = infer_roles(stac_bands)
    if not mapping:
        # 波長が無いデータセット(SAR・土地被覆・気象など)。
        # 実バンド名をそのまま役割名にして単バンド表示だけ可能にする。
        mapping = {b["name"]: b["name"] for b in stac_bands}
    roles = _bands_with_scaling(stac_bands, mapping)

    # gsd はデータセットによってスカラー(10)とリスト([10])の両方があるので均す
    gsds: list[float] = []
    for b in stac_bands:
        g = b.get("gsd")
        if isinstance(g, list):
            gsds.extend(x for x in g if isinstance(x, (int, float)))
        elif isinstance(g, (int, float)):
            gsds.append(g)
    asset_type = (
        "image" if stac.get("gee:type") == "image" else "image_collection"
    )
    categories = stac.get("gee:categories") or []

    return Source(
        id=f"stac:{ee_id}",
        label=stac.get("title") or ee_id,
        asset=ee_id,
        asset_type=asset_type,
        kind="stac",
        roles=roles,
        temporal=asset_type == "image_collection",
        filters=_cloud_filter(summaries.get("gee:schema", [])),
        native_scale=int(min(gsds)) if gsds else 30,
        value_range=_value_range(stac, roles),
        time_range=_time_range(stac),
        description=(stac.get("description") or "").split("\n")[0][:300],
        category=categories[0] if categories else "",
    )


def ee_id_from_stem(provider_key: str, stem: str) -> str:
    """STAC のファイル名から EE アセットIDを復元する。

    ファイル名は "/" を "_" に潰したものなので機械的な復元はできない。
    STAC 本体を1回引いて正式な id を取り出す(URL 単位で lru_cache される
    ため、直後の source_from_stac() は同じキャッシュに当たる)。
    """
    return _get_json(f"{BASE}/{provider_key}/{stem}.json")["id"]
