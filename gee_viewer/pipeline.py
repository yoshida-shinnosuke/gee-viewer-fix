"""パイプラインの型定義と合成ロジック。

    Source(データセット) → Recipe(バンド合成・指数) → Analysis(解析)×n → 可視化

各段は「何を要求し、何を出すか」を宣言する。UI はその宣言を見て、上流の
出力と相性が合う選択肢だけを次の段に提示する(例: Sentinel-1 には red/nir
が無いので NDVI もトゥルーカラーも選択肢に現れない)。

処理はすべて Earth Engine のサーバー側で完結し、ピクセルはダウンロードしない。

## バンドの「役割」

このモジュールの肝は、実バンド名(B4, SR_B4, sur_refl_b01, ...)を衛星共通の
役割名(red, nir, swir1, ...)に正規化することにある。Source が済ませた時点で
バンド名は役割名になっているので、下流の指数は

    normalizedDifference(["nir", "red"])

と1回書くだけで Sentinel-2 でも Landsat でも MODIS でも動く。
同時に scale/offset を適用して物理量(反射率 0-1 など)に揃えるため、
可視化パラメータも衛星をまたいで共有できる。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

import ee

# 画像の「形」。段どうしの相性判定に使う。
Kind = Literal["single", "rgb", "multi"]


# ------------------------------------------------------------------- 型定義


@dataclass(frozen=True)
class Band:
    """役割に割り当てられた実バンド。

    scale/offset は生の格納値を物理量に直す係数(GEE の STAC が公開している
    gee:scale / gee:offset に対応)。例: Landsat C2 L2 の反射率は
    生値 × 2.75e-5 − 0.2。
    """

    name: str
    scale: float = 1.0
    offset: float = 0.0


@dataclass(frozen=True)
class ParamSpec:
    """UI ウィジェットを自動生成するための宣言。

    各段が自分の必要なパラメータをこれで宣言するので、app.py 側に
    「この解析のときだけスライダーを出す」という分岐を書かずに済む。
    """

    name: str
    label: str
    kind: Literal["int", "float", "select", "bool"]
    default: Any
    min: float | None = None
    max: float | None = None
    step: float | None = None
    options: tuple = ()
    # 選択肢が Source に依存する場合(例: 単バンド表示のバンド選択)に使う
    options_from_source: Callable[["Source"], tuple] | None = None
    help: str = ""


@dataclass(frozen=True)
class Filter:
    """ImageCollection に対する絞り込み。param は UI に出すスライダー等。"""

    param: ParamSpec
    apply: Callable[[Any, Any], Any]  # (ImageCollection, 値) -> ImageCollection
    # 書き出し用。ee.Filter を作る式のソースを返す(絞り込み不要なら None)
    code: Callable[[Any], str | None]


@dataclass(frozen=True)
class Source:
    """データセット(衛星)。パイプラインの第1段。"""

    id: str
    label: str
    asset: str  # Earth Engine のアセットID
    asset_type: Literal["image_collection", "image"] = "image_collection"
    kind: str = "other"  # optical / sar / dem / landcover / other
    roles: dict[str, Band] = field(default_factory=dict)
    temporal: bool = True  # 期間フィルタが意味を持つか
    filters: tuple[Filter, ...] = ()
    # UI に出さない固定の絞り込み。roles に挙げたバンドが必ず存在するよう
    # コレクションを揃える用途(例: Sentinel-1 で VV と VH の両方を持つ
    # シーンだけに限定する)。
    prefilter: Callable[[Any], Any] | None = None
    cloud_mask: Callable[[Any], Any] | None = None  # 画像ごとの雲マスク
    native_scale: int = 30  # 解像度(m)。k-means のサンプリング等に使う
    # 役割バンドの自然な表示レンジ。合成画像・単バンド表示の既定 vis になる。
    value_range: tuple[float, float] = (0.0, 0.3)
    # データが存在する期間 (YYYY-MM-DD, YYYY-MM-DD)。UI の日付初期値に使う。
    # 範囲外の期間を指定すると空コレクションになり、EE は「バンドが無い」
    # という分かりにくいエラーを返すため、事前に知っておく必要がある。
    time_range: tuple[str | None, str | None] = (None, None)
    description: str = ""
    vis_presets: tuple = ()  # STAC 由来の公式可視化プリセット
    category: str = ""

    def has(self, *roles: str) -> bool:
        return all(r in self.roles for r in roles)


@dataclass(frozen=True)
class Recipe:
    """バンド合成・指数。パイプラインの第2段。

    requires に挙げた役割をすべて持つ Source のときだけ UI に現れる。
    """

    id: str
    label: str
    build: Callable[[ee.Image, dict, Source], ee.Image]
    # 書き出し用。build と同じ処理を行うコードを文字列で返す。変数 image を
    # 読み書きする文(複数行可)。build と対で定義し、可能な限り同じ引数から
    # 両方を生成することでズレを防ぐ(recipes.py のファクトリを参照)。
    code: Callable[[dict, Source], str]
    requires: frozenset[str] = frozenset()
    produces: Kind = "single"
    # None の場合は Source.value_range から自動で組み立てる
    vis: dict | None = None
    params: tuple[ParamSpec, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class Analysis:
    """解析。パイプラインの第3段(0個以上を直列に繋げられる)。

    accepts が入力画像の形と合うときだけ UI に現れる。
    """

    id: str
    label: str
    build: Callable[[ee.Image, dict, dict], ee.Image]  # (画像, パラメータ, 文脈)
    # 書き出し用。(パラメータ, 文脈) -> コード文字列。文脈には領域を指す
    # 変数名 region と解像度 scale が入る。
    code: Callable[[dict, dict], str]
    accepts: Kind | Literal["any"] = "any"
    produces: Kind | Literal["same"] = "same"
    vis: dict | None = None  # None なら上流の可視化を引き継ぐ
    params: tuple[ParamSpec, ...] = ()
    # 出力の値域が事前に決まらない解析(勾配・ラプラシアン等)。UI 側で
    # 自動ストレッチを強制する。これを付けないと上流の可視化(例: NDVI の
    # -1〜1 と発散配色)をそのまま引き継いでしまい、意味の無い絵になる。
    needs_stretch: bool = False
    description: str = ""


# --------------------------------------------------------------- 合成の実装

REDUCERS: dict[str, Callable[[Any], ee.Image]] = {
    "median": lambda col: col.median(),
    "mean": lambda col: col.mean(),
    "mosaic": lambda col: col.mosaic(),
    "min": lambda col: col.min(),
    "max": lambda col: col.max(),
    "first": lambda col: col.first(),
}

REDUCER_PARAM = ParamSpec(
    name="reducer",
    label="期間の合成方法",
    kind="select",
    default="median",
    options=tuple(REDUCERS),
    help="期間中の複数シーンを1枚にまとめる方法。median は雲の影響を受けにくい。",
)

CLOUD_MASK_PARAM = ParamSpec(
    name="cloud_mask",
    label="雲をマスクする",
    kind="bool",
    default=True,
    help="QA バンドを使って雲・雲影のピクセルを除外する(合成前に適用)。",
)


def source_params(src: Source) -> list[ParamSpec]:
    """この Source が UI に出すべきパラメータ一覧。"""
    specs: list[ParamSpec] = []
    if src.asset_type == "image_collection":
        specs.append(REDUCER_PARAM)
    if src.cloud_mask is not None:
        specs.append(CLOUD_MASK_PARAM)
    specs.extend(f.param for f in src.filters)
    return specs


def _to_roles(img: ee.Image, roles: dict[str, Band]) -> ee.Image:
    """実バンド名を役割名に付け替え、scale/offset を適用する。"""
    bands = []
    for role, band in roles.items():
        b = img.select([band.name])
        if band.scale != 1.0:
            b = b.multiply(band.scale)
        if band.offset != 0.0:
            b = b.add(band.offset)
        bands.append(b.rename(role))
    return ee.Image.cat(bands)


def source_image(
    src: Source, region, start: str, end: str, params: dict
) -> ee.Image:
    """Source を1枚の ee.Image に解決する(バンド名は役割名になる)。"""
    if src.asset_type == "image":
        img = ee.Image(src.asset)
    else:
        col = ee.ImageCollection(src.asset).filterBounds(region)
        if src.temporal:
            col = col.filterDate(start, end)
        if src.prefilter is not None:
            col = src.prefilter(col)
        for f in src.filters:
            col = f.apply(col, params.get(f.param.name, f.param.default))
        if src.cloud_mask is not None and params.get("cloud_mask", True):
            col = col.map(src.cloud_mask)
        reducer = params.get("reducer", REDUCER_PARAM.default)
        img = REDUCERS[reducer](col)
    return _to_roles(ee.Image(img), src.roles)


def default_vis(src: Source) -> dict:
    """Recipe が vis を指定しないときの既定可視化。"""
    lo, hi = src.value_range
    return {"min": lo, "max": hi}


def resolve_vis(
    src: Source, recipe: Recipe, analyses: list[Analysis]
) -> tuple[dict, Kind]:
    """可視化パラメータと出力の形を決める。EE を呼ばない純粋な計算。

    「最後に vis を宣言した段」のものが勝つ。どの段も宣言しなければ
    Source.value_range から組み立てる(任意のデータセットでも破綻しないため)。

    実行時(build)とコード書き出し(codegen)の両方がこれを使うので、
    画面に出ている配色と書き出したコードの配色は必ず一致する。
    """
    vis = recipe.vis if recipe.vis is not None else default_vis(src)
    kind: Kind = recipe.produces
    for analysis in analyses:
        if analysis.vis is not None:
            vis = analysis.vis
        if analysis.produces != "same":
            kind = analysis.produces
    return dict(vis), kind


def build(
    src: Source,
    recipe: Recipe,
    analyses: list[tuple[Analysis, dict]],
    region,
    start: str,
    end: str,
    source_params_: dict,
    recipe_params: dict,
) -> tuple[ee.Image, dict, Kind]:
    """パイプライン全体を1枚の ee.Image と可視化パラメータに解決する。"""
    img = source_image(src, region, start, end, source_params_)
    img = recipe.build(img, recipe_params, src)

    ctx = {"region": region, "scale": src.native_scale, "source": src}
    for analysis, params in analyses:
        img = analysis.build(img, params, ctx)

    vis, kind = resolve_vis(src, recipe, [a for a, _ in analyses])
    return img, vis, kind


def compatible_recipes(src: Source, recipes: Iterable[Recipe]) -> list[Recipe]:
    """Source が持つ役割で実行できる Recipe だけを返す。"""
    return [r for r in recipes if r.requires <= set(src.roles)]


def compatible_analyses(
    kind: Kind, analyses: Iterable[Analysis]
) -> list[Analysis]:
    """入力画像の形と相性が合う Analysis だけを返す。"""
    return [a for a in analyses if a.accepts in ("any", kind)]
