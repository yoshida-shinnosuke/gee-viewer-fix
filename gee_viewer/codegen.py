"""パイプライン定義から実行可能な Python コードを生成する。

このアプリの目的(docs/vision.md 方針3「出口を塞がない」)の中核。
GUI で組んだ設定をそのまま持ち帰れるコードにして、GUI の天井に達した
ユーザーがローカルで開発を続けられるようにする。

## 設計方針

- **生成コードは gee_viewer に依存しない。** 依存させると「持ち帰って
  自由に開発する」ができなくなる。`earthengine-api` と表示ライブラリだけで動く。
- **GEE API をそのまま書く。** 独自のラッパーを噛ませない。GUI で覚えた
  概念とコードの概念が一致していることが、移行の易しさそのものになる。
- **画面と一致することを保証する。** 各段が持つ `code` は `build` と対で
  定義されており、生成コードが組む計算グラフはアプリが表示しているものと
  一致する(tests で ee の serialize() を突き合わせて検証している)。

## 出力形式

- `.py`   : folium で地図HTMLを書き出すスクリプト
- `.ipynb`: geemap で対話的に表示するノートブック
  (`Map.addLayer` は GEE Code Editor と同じ書き味)
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime
from typing import Any, Callable

from . import analyses, pipeline, recipes, sources
from .pipeline import Source

REGION_VAR = "REGION"


# ------------------------------------------------------------ 補助関数群


AUTO_STRETCH_SRC = '''def auto_stretch(image, region, scale):
    """実データの2〜98パーセンタイルで表示レンジを決める。

    取得するのは統計値(数値が数個)だけで、ピクセルは取得しない。
    """
    stats = image.reduceRegion(
        reducer=ee.Reducer.percentile([2, 98]),
        geometry=region,
        scale=scale,
        bestEffort=True,
        maxPixels=int(1e8),
    ).getInfo()
    lo = [v for k, v in stats.items() if k.endswith('_p2') and v is not None]
    hi = [v for k, v in stats.items() if k.endswith('_p98') and v is not None]
    return {'min': min(lo), 'max': max(hi)}'''


FOLIUM_HELPER_SRC = '''def add_ee_layer(fmap, image, vis, name, opacity=1.0):
    """ee.Image をタイルURLとして folium に追加する。

    ピクセルをダウンロードせず、ブラウザが Earth Engine から直接タイルを
    取得する。ズーム・パンしても手元の通信量はほぼ増えない。
    """
    map_id = image.getMapId(vis)
    folium.TileLayer(
        tiles=map_id['tile_fetcher'].url_format,
        attr='Google Earth Engine',
        name=name,
        overlay=True,
        opacity=opacity,
    ).add_to(fmap)'''


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line if line else line for line in text.splitlines())


# --------------------------------------------------------- 各段のコード化


def _collection_code(src: Source, params: dict, start: str, end: str) -> list[str]:
    """Source をコレクション → 合成画像にするまでのコード。"""
    lines: list[str] = []
    if src.asset_type == "image":
        lines.append(f"composite = ee.Image({src.asset!r})")
        return lines

    chain = [f"    ee.ImageCollection({src.asset!r})", f"    .filterBounds({REGION_VAR})"]
    if src.temporal:
        chain.append(f"    .filterDate({start!r}, {end!r})")

    filter_exprs = [
        expr
        for f in src.filters
        if (expr := f.code(params.get(f.param.name, f.param.default)))
    ]

    # 絞り込みの適用順序は pipeline.source_image と揃える必要がある。
    # 順序が違っても結果は同じだが計算グラフが変わり、「画面と書き出しが
    # 一致する」という保証が崩れるため。prefilter が先、UIフィルタが後。
    if src.prefilter is None:
        chain += [f"    .filter({expr})" for expr in filter_exprs]
        lines.append("collection = (\n" + "\n".join(chain) + "\n)")
    else:
        lines.append("collection = (\n" + "\n".join(chain) + "\n)")
        lines.append(
            f"collection = {src.prefilter.__name__}(collection)"
            "  # バンドが揃うシーンに限定"
        )
        for expr in filter_exprs:
            lines.append(f"collection = collection.filter({expr})")

    if src.cloud_mask is not None and params.get("cloud_mask", True):
        lines.append(f"collection = collection.map({src.cloud_mask.__name__})")

    reducer = params.get("reducer", "median")
    lines.append(f"composite = collection.{reducer}()")
    return lines


def _roles_code(src: Source) -> list[str]:
    """実バンド名 → 役割名の付け替えと scale/offset 適用のコード。"""
    parts = []
    for role, band in src.roles.items():
        expr = f"composite.select([{band.name!r}])"
        if band.scale != 1.0:
            expr += f".multiply({band.scale!r})"
        if band.offset != 0.0:
            expr += f".add({band.offset!r})"
        parts.append(f"    {expr}.rename({role!r}),")
    return [
        "# バンド名を衛星共通の役割名に正規化し、物理量に変換する",
        "image = ee.Image.cat([",
        *parts,
        "])",
    ]


def _helpers_for(src: Source) -> list[Callable]:
    """生成コードに丸ごと埋め込む必要のある関数。

    inspect.getsource() でこのリポジトリの定義をそのまま写すので、
    実装を変えても生成コードが古くならない。
    """
    fns = []
    if src.prefilter is not None:
        fns.append(src.prefilter)
    if src.cloud_mask is not None:
        fns.append(src.cloud_mask)
    return fns


def _layer_code(layer: dict) -> tuple[list[str], list[Callable], bool]:
    """レイヤー1枚分のコード行、必要な補助関数、自動ストレッチ使用有無。"""
    spec = layer["spec"]
    src = sources.resolve(spec["source"])
    recipe = recipes.BY_ID[spec["recipe"]]
    ctx = {"region": REGION_VAR, "scale": src.native_scale}

    lines: list[str] = []
    lines += _collection_code(src, spec["source_params"], layer["start"], layer["end"])
    lines.append("")
    lines += _roles_code(src)
    lines.append("")
    lines.append(f"# {recipe.label}")
    lines.append(recipe.code(spec["recipe_params"], src))

    chain = [analyses.BY_ID[s["id"]] for s in spec["analyses"]]
    for analysis, step in zip(chain, spec["analyses"]):
        lines.append("")
        lines.append(f"# {analysis.label}")
        lines.append(analysis.code(step["params"], ctx))

    # 実行時と同じ関数で解決するので、画面の配色と書き出しの配色は一致する
    vis, _ = pipeline.resolve_vis(src, recipe, chain)
    lines.append("")
    lines.append(f"vis = {vis!r}")
    if layer["stretch"]:
        lines.append(
            f"vis = {{**vis, **auto_stretch(image, {REGION_VAR}, {src.native_scale})}}"
        )
    return lines, _helpers_for(src), bool(layer["stretch"])


# --------------------------------------------------------------- 組み立て


def _preamble(layers: list[dict], project: str) -> str:
    described = "\n".join(f"    {i + 1}. {ly['label']}" for i, ly in enumerate(layers))
    return f'''"""GEE Viewer が書き出したコード。

このアプリの GUI で組んだ設定を、そのまま動く Python コードにしたもの。
以降は自由に書き換えて使える(このファイルは gee_viewer に依存しない)。

含まれるレイヤー:
{described}

生成日時: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

必要なもの:
    pip install earthengine-api folium
    earthengine authenticate   # 初回のみ
"""'''


def _setup_lines(project: str, lon: float, lat: float, buffer_km: int) -> list[str]:
    return [
        f"ee.Initialize(project={project!r})",
        "",
        f"# 解析領域: 中心 ({lat}, {lon}) から半径 {buffer_km}km の矩形",
        f"{REGION_VAR} = (",
        f"    ee.Geometry.Point([{lon}, {lat}])",
        f"    .buffer({buffer_km * 1000})",
        "    .bounds()",
        ")",
    ]


def _collect_helpers(layers: list[dict]) -> tuple[list[str], bool]:
    """全レイヤーで必要な補助関数のソースを重複なく集める。"""
    seen: dict[str, str] = {}
    stretch = False
    for layer in layers:
        _, fns, used = _layer_code(layer)
        stretch = stretch or used
        for fn in fns:
            seen.setdefault(fn.__name__, inspect.getsource(fn).rstrip())
    return list(seen.values()), stretch


def generate_py(layers: list[dict], lon: float, lat: float, buffer_km: int,
                zoom: int, project: str) -> str:
    """folium で地図HTMLを書き出すスクリプトを生成する。"""
    helpers, needs_stretch = _collect_helpers(layers)

    out: list[str] = [_preamble(layers, project), "", "import ee", "import folium", ""]
    out += _setup_lines(project, lon, lat, buffer_km)
    out += ["", "", FOLIUM_HELPER_SRC]
    if needs_stretch:
        out += ["", "", AUTO_STRETCH_SRC]
    for helper in helpers:
        out += ["", "", helper]

    out += ["", "", f"fmap = folium.Map(location=[{lat}, {lon}], zoom_start={zoom})"]

    for i, layer in enumerate(layers):
        lines, _, _ = _layer_code(layer)
        out += [
            "",
            "",
            "# " + "=" * 68,
            f"# レイヤー{i + 1}: {layer['label']}",
            "# " + "=" * 68,
        ]
        out += lines
        out += [
            "add_ee_layer(",
            "    fmap,",
            f"    image.clip({REGION_VAR}),",
            "    vis,",
            f"    {layer['label']!r},",
            f"    opacity={layer['opacity']!r},",
            ")",
        ]

    out += [
        "",
        "",
        "folium.LayerControl(collapsed=False).add_to(fmap)",
        "fmap.save('map.html')",
        "print('map.html を書き出しました。ブラウザで開いてください。')",
    ]
    return "\n".join(out) + "\n"


def generate_ipynb(layers: list[dict], lon: float, lat: float, buffer_km: int,
                   zoom: int, project: str) -> str:
    """geemap で対話的に表示するノートブックを生成する。"""
    helpers, needs_stretch = _collect_helpers(layers)
    cells: list[dict] = []

    def md(text: str) -> None:
        cells.append({"cell_type": "markdown", "metadata": {},
                      "source": text.splitlines(keepends=True)})

    def code(text: str) -> None:
        cells.append({"cell_type": "code", "execution_count": None, "metadata": {},
                      "outputs": [], "source": text.splitlines(keepends=True)})

    layer_list = "\n".join(f"{i + 1}. {ly['label']}" for i, ly in enumerate(layers))
    md(
        f"""# GEE Viewer が書き出したノートブック

GUI で組んだ設定をそのまま動くコードにしたもの。
以降は自由に書き換えて使えます(このノートブックは gee_viewer に依存しません)。

## 含まれるレイヤー

{layer_list}

生成日時: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## 必要なもの

```
pip install earthengine-api geemap
earthengine authenticate   # 初回のみ
```
"""
    )

    code("import ee\nimport geemap\n\n" + "\n".join(_setup_lines(project, lon, lat, buffer_km)))

    if helpers or needs_stretch:
        md("## 補助関数")
        blocks = list(helpers)
        if needs_stretch:
            blocks.insert(0, AUTO_STRETCH_SRC)
        code("\n\n\n".join(blocks))

    md("## 地図")
    code(f"Map = geemap.Map(center=[{lat}, {lon}], zoom={zoom})\nMap")

    for i, layer in enumerate(layers):
        lines, _, _ = _layer_code(layer)
        md(f"## レイヤー{i + 1}: {layer['label']}")
        body = "\n".join(lines)
        body += (
            f"\n\nMap.addLayer(\n"
            f"    image.clip({REGION_VAR}),\n"
            f"    vis,\n"
            f"    {layer['label']!r},\n"
            f"    True,\n"
            f"    {layer['opacity']!r},\n"
            f")"
        )
        code(body)

    md("表示を確認するには、上の `Map` セルまでスクロールして戻ってください。")
    code("Map")

    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3", "language": "python", "name": "python3"
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    return json.dumps(notebook, ensure_ascii=False, indent=1)


def describe(layers: list[dict]) -> str:
    """書き出し対象の要約(UI 表示用)。"""
    return "\n".join(f"{i + 1}. {ly['label']}" for i, ly in enumerate(layers))
