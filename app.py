"""GEE Viewer — Google Earth Engine 汎用ビューア (Streamlit).

サイドバーを上から下へ辿ると1本の処理パイプラインが決まる:

    データセット → バンド合成・指数 → 解析(0段以上) → 可視化

組み上げたパイプラインは「レイヤーとして追加」で地図に固定でき、
複数を重ね合わせられる(例: トゥルーカラーの上に海岸線抽出を重ねる)。

起動: streamlit run app.py
"""

import json
import os
from datetime import date, timedelta

# pyarrow 25 同梱の mimalloc は、「pyarrow を最初に読み込んだスレッドが終了した後、
# 別スレッドで Arrow がメモリを確保する」と SIGSEGV で落ちる(apache/arrow#50471)。
# Streamlit は再実行ごとにスクリプト用スレッドを作り直すので、pandas / Streamlit
# 経由で Arrow を使った2回目の実行でちょうどこの条件になる。pyarrow の初回
# 読み込みより前にシステムアロケータを指定して回避する(修正版が出たら削除可)。
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

import altair as alt
import ee
import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from gee_viewer import analyses, auth, availability, codegen, geocode, pipeline, recipes, sources, stac, ui

st.set_page_config(page_title="GEE Viewer", page_icon="🌍", layout="wide")

# 1x1 の透明PNG。全タイルがこれになるので実質「背景なし」になる。
_BLANK_TILE = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
    "AAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)

# 地図右上の LayerControl でラジオ選択できる背景地図。
# CartoDB 系は API key 制約があり、キーを持たない運用では使えないため除外。
# (表示名, タイル指定, 帰属表示)
BASEMAPS = (
    ("OpenStreetMap", "OpenStreetMap", "OpenStreetMap"),
    (
        "衛星画像 (Esri)",
        "https://server.arcgisonline.com/ArcGIS/rest/services/"
        "World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "Esri",
    ),
    ("背景なし", _BLANK_TILE, " "),
)


# ---------------------------------------------------------------- EE 初期化


@st.cache_resource
def init_ee() -> str:
    """EE初期化は重いのでプロセスで1回だけ実行。"""
    return auth.initialize()


try:
    auth_mode = init_ee()
except auth.ProjectNotSetError as e:
    st.error(str(e))
    st.stop()
except Exception as e:
    st.error(
        "Earth Engine の初期化に失敗しました。ローカル利用の場合は "
        "`earthengine authenticate` を先に実行してください。\n\n"
        f"詳細: {e}"
    )
    st.stop()


# ------------------------------------------------------------- EE 呼び出し


def _region(lon: float, lat: float, buffer_km: int):
    return ee.Geometry.Point([lon, lat]).buffer(buffer_km * 1000).bounds()


def _build_from_spec(spec: dict, region, start: str, end: str):
    """JSON化されたパイプライン定義を ee.Image に解決する。

    パイプラインを「ただのデータ」として持つことで、そのまま
    @st.cache_data のキーに使える(関数オブジェクトはキーにできない)。
    """
    src = sources.resolve(spec["source"])
    recipe = recipes.BY_ID[spec["recipe"]]
    chain = [
        (analyses.BY_ID[s["id"]], s["params"]) for s in spec["analyses"]
    ]
    return pipeline.build(
        src, recipe, chain, region, start, end,
        spec["source_params"], spec["recipe_params"],
    )


@st.cache_data(show_spinner=False)
def auto_stretch(spec_json: str, lon, lat, buffer_km, start, end) -> dict:
    """表示レンジを実データの 2〜98 パーセンタイルから決める。

    ピクセルではなく統計値だけを取得する(数値が数個返るだけ)。任意の
    データセットを扱う以上、値域を事前に知り得ないケースがあるため用意。
    """
    region = _region(lon, lat, buffer_km)
    img, _, _ = _build_from_spec(json.loads(spec_json), region, start, end)
    stats = img.reduceRegion(
        reducer=ee.Reducer.percentile([2, 98]),
        geometry=region,
        scale=sources.resolve(json.loads(spec_json)["source"]).native_scale,
        bestEffort=True,
        maxPixels=int(1e8),
    ).getInfo()
    lo = [v for k, v in stats.items() if k.endswith("_p2") and v is not None]
    hi = [v for k, v in stats.items() if k.endswith("_p98") and v is not None]
    if not lo or not hi:
        raise ValueError("統計値を計算できませんでした(領域内が全て無効値の可能性)。")
    return {"min": min(lo), "max": max(hi)}


@st.cache_data(show_spinner="Earth Engine からタイルURLを取得中...")
def tile_url(spec_json: str, lon, lat, buffer_km, start, end, stretch: bool) -> str:
    """パイプラインをタイルURLに解決する。

    ピクセルはダウンロードしない。getMapId が返すURLをブラウザが直接
    叩くので、ズーム・パンはブラウザと EE の間で完結する。
    """
    region = _region(lon, lat, buffer_km)
    img, vis, _ = _build_from_spec(json.loads(spec_json), region, start, end)
    if stretch:
        vis = {**vis, **auto_stretch(spec_json, lon, lat, buffer_km, start, end)}
    return ee.Image(img).clip(region).getMapId(vis)["tile_fetcher"].url_format


@st.cache_data(show_spinner=False)
def scene_count(source_id: str, params_json: str, lon, lat, buffer_km, start, end) -> int:
    """期間・領域・フィルタに合う画像の枚数(availability.CAP で打ち切り)。"""
    src_ = sources.resolve(source_id)
    return availability.count(
        src_, _region(lon, lat, buffer_km), start, end, json.loads(params_json)
    )


@st.cache_data(show_spinner=False)
def scene_dates(source_id: str, params_json: str, lon, lat, buffer_km) -> list[str] | None:
    """その地点の撮影日の一覧(期間は問わない)。多すぎる場合は None。"""
    src_ = sources.resolve(source_id)
    return availability.scene_dates(
        src_, _region(lon, lat, buffer_km), json.loads(params_json)
    )


# ------------------------------------------------------- サイドバー: 第1段

st.sidebar.title("🌍 GEE Viewer")
st.sidebar.caption(f"認証方式: {auth_mode}")

st.sidebar.header("1. データセット")
mode = st.sidebar.radio(
    "選び方", ["よく使う", "全カタログを辿る", "IDを直接入力"], horizontal=False,
    help="「よく使う」以外は GEE の STAC からメタデータを取得して自動設定します。",
)

src = None
if mode == "よく使う":
    category = st.sidebar.selectbox("カテゴリ", sources.CATEGORIES)
    choices = {s.label: s for s in sources.in_category(category)}
    src = choices[st.sidebar.selectbox("データセット", list(choices))]
else:
    try:
        if mode == "全カタログを辿る":
            providers = {p[0]: p[1] for p in stac.list_providers()}
            prov_key = providers[st.sidebar.selectbox("提供元", list(providers))]
            datasets = {d[0]: d[1] for d in stac.list_datasets(prov_key)}
            stem = datasets[st.sidebar.selectbox("データセット", list(datasets))]
            ee_id = stac.ee_id_from_stem(prov_key, stem)
        else:
            ee_id = st.sidebar.text_input(
                "EEアセットID", "COPERNICUS/S2_SR_HARMONIZED",
                help="例: NASA/GDDP-CMIP6, ESA/WorldCover/v200",
            ).strip()
        if ee_id:
            src = sources.resolve(f"stac:{ee_id}")
            st.sidebar.caption(f"`{ee_id}`")
    except Exception as e:
        st.sidebar.error(f"STAC から取得できませんでした: {e}")

if src is None:
    st.info("データセットを選んでください。")
    st.stop()

st.sidebar.caption(src.description or "")
if src.roles:
    st.sidebar.caption("認識されたバンド: " + ", ".join(src.roles))

source_params = ui.render_params(
    pipeline.source_params(src), f"src:{src.id}", src, st.sidebar
)


# ------------------------------------------------------- サイドバー: 第2段

st.sidebar.header("2. バンド合成・指数")
available = pipeline.compatible_recipes(src, recipes.RECIPES)
recipe_choices = {r.label: r for r in available}
recipe = recipe_choices[
    st.sidebar.selectbox(
        "内容", list(recipe_choices),
        help="このデータセットが持つバンドで実行できるものだけを表示しています。",
    )
]
st.sidebar.caption(recipe.description or "")
recipe_params = ui.render_params(
    recipe.params, f"rec:{src.id}:{recipe.id}", src, st.sidebar
)


# ------------------------------------------------------- サイドバー: 第3段

st.sidebar.header("3. 解析")
n_steps = st.sidebar.number_input(
    "段数", 0, 4, 0, help="0 なら合成画像をそのまま表示。上から順に適用されます。"
)

kind = recipe.produces
chain: list[dict] = []
chain_labels: list[str] = []
force_stretch = False
for i in range(int(n_steps)):
    usable = pipeline.compatible_analyses(kind, analyses.ANALYSES)
    if not usable:
        st.sidebar.caption("これ以上つなげられる解析はありません。")
        break
    picks = {a.label: a for a in usable}
    step = picks[st.sidebar.selectbox(f"解析 {i + 1}", list(picks), key=f"an{i}")]
    st.sidebar.caption(step.description or "")
    params = ui.render_params(step.params, f"an{i}:{step.id}", src, st.sidebar)
    chain.append({"id": step.id, "params": params})
    chain_labels.append(step.label)
    force_stretch = force_stretch or step.needs_stretch
    if step.produces != "same":
        kind = step.produces


# ------------------------------------------------- サイドバー: 期間・領域

st.sidebar.header("4. 期間と領域")
if src.temporal:
    # データが存在しない期間を指定すると空コレクションになり、EE は
    # 「バンドが無い」という分かりにくいエラーを返す。STAC が持つ提供期間を
    # 使って初期値をその範囲内に収め、範囲外なら警告する。
    lo, hi = src.time_range
    avail_start = date.fromisoformat(lo) if lo else date(1972, 1, 1)
    avail_end = min(date.fromisoformat(hi), date.today()) if hi else date.today()
    if lo or hi:
        st.sidebar.caption(f"データのある期間: {lo or '?'} 〜 {hi or '?'}")
    default_end = max(avail_start, avail_end)
    if (default_end - avail_start).days <= 365 * 3:
        # 年次プロダクトのように画像が数枚しか無いものは、直近90日で切ると
        # 1枚も掴めないことがある。提供期間が短いデータセットは全期間を既定に。
        default_start = avail_start
    else:
        default_start = max(avail_start, default_end - timedelta(days=90))
    start_date = st.sidebar.date_input("開始日", value=default_start)
    end_date = st.sidebar.date_input("終了日", value=default_end)
    if end_date < avail_start or start_date > avail_end:
        st.sidebar.warning("選択期間はこのデータセットの提供期間から外れています。")
else:
    st.sidebar.caption("このデータセットは時間軸を持たないため期間指定は無視されます。")
    start_date, end_date = date(2000, 1, 1), date.today()

if "lat" not in st.session_state:
    st.session_state.lat = 35.545
if "lon" not in st.session_state:
    st.session_state.lon = 134.225


def _apply_geocode_search(query: str) -> None:
    """「検索」ボタンが押されたときの処理。1位の候補へ緯度経度を移動する。

    複数候補は st.session_state.geocode_results に残しておき、
    下の selectbox で選び直せるようにする。
    """
    if not query.strip():
        st.sidebar.warning("場所名を入力してください。")
        st.session_state.geocode_results = ()
        return
    try:
        results = geocode.search(query)
    except Exception as e:
        st.sidebar.error(f"検索に失敗しました(OpenStreetMap Nominatim): {e}")
        st.session_state.geocode_results = ()
        return
    st.session_state.geocode_results = results
    if not results:
        st.sidebar.warning("見つかりませんでした。表記を変えて試してください。")
        return
    st.session_state.lat = results[0].lat
    st.session_state.lon = results[0].lon


st.sidebar.caption("場所名で検索するか、下で緯度・経度を直接指定してください。")
place_query = st.sidebar.text_input(
    "場所を検索 (OpenStreetMap)", placeholder="例: 鳥取砂丘"
)
if st.sidebar.button("検索"):
    _apply_geocode_search(place_query)

geocode_results = st.session_state.get("geocode_results") or ()
if len(geocode_results) > 1:
    picked_place = st.sidebar.selectbox(
        "候補が複数あります。違う場所ならこちらから選択:",
        geocode_results,
        format_func=lambda p: p.name,
        key="geocode_pick",
    )
    if st.sidebar.button("この候補を使う"):
        st.session_state.lat = picked_place.lat
        st.session_state.lon = picked_place.lon

st.sidebar.caption("初期値は鳥取砂丘。数値を変えると移動します。")
lat = st.sidebar.number_input("緯度", key="lat", format="%.4f")
lon = st.sidebar.number_input("経度", key="lon", format="%.4f")
buffer_km = st.sidebar.slider("領域サイズ (km)", 1, 50, 5)
zoom = st.sidebar.slider("ズーム", 8, 16, 13)


def render_scene_count(container) -> tuple[bool, list[str] | None]:
    """選択中の条件に合う画像の枚数を表示し、0 枚なら前後で画像のある日を案内する。

    STAC の提供期間だけでは「この場所にいつ画像があるか」が分からず、
    撮影がまばらなデータセットでは無言で空の地図になってしまうため。
    戻り値は (問い合わせに成功したか, その地点の撮影日一覧) で、
    撮影日一覧は地図の下の月別グラフ(render_monthly_chart)でも使う。
    """
    params_json = json.dumps(source_params, sort_keys=True)
    try:
        with container.spinner("画像の有無を確認中..."):
            n = scene_count(
                src.id, params_json, lon, lat, buffer_km,
                str(start_date), str(end_date),
            )
            dates = scene_dates(src.id, params_json, lon, lat, buffer_km)
    except Exception as e:
        container.caption(f"画像の有無を確認できませんでした: {e}")
        return False, None

    if n > availability.CAP:
        container.caption(f"この期間・領域の画像: {availability.CAP:,} 枚以上")
    elif n > 0:
        container.caption(f"この期間・領域の画像: {n:,} 枚")
    else:
        msg = "この期間・領域・条件に合う画像がありません。"
        if dates:
            before, after = availability.nearest_dates(dates, start_date, end_date)
            hints = []
            if before:
                hints.append(f"直前 {before}")
            if after:
                hints.append(f"直後 {after}")
            msg += "\n\n近くで画像がある日: " + " / ".join(hints)
        elif dates == []:
            msg += "この地点にはどの時期の画像もありません(雲量上限なども確認してください)。"
        container.warning(msg)
    return True, dates


def render_monthly_chart(dates: list[str] | None) -> None:
    """その地点の月別撮影枚数を棒グラフで表示する(選択中の期間を帯で重ねる)。"""
    st.subheader("この地点の撮影状況")
    if dates is None:
        st.caption(
            f"撮影枚数が多いため({availability.CAP:,} 枚超)月別集計は省略しました。"
            "ほぼ常時データがあります。"
        )
        return
    if not dates:
        st.caption("この地点・条件に合う画像はどの時期にもありません。")
        return

    counts = availability.monthly_counts(dates)
    df = pd.DataFrame(
        {"month": pd.to_datetime(list(counts)), "count": list(counts.values())}
    )
    # 選択中の期間を帯で重ね、グラフのどこを見ているか分かるようにする
    period = pd.DataFrame({"start": [pd.Timestamp(start_date)], "end": [pd.Timestamp(end_date)]})
    # Vega-Lite の既定の時間軸は「2017」「July」のように年と月名が混在するので、
    # 表示期間の長さに応じて目盛り間隔と書式を明示する。
    span_months = len(pd.period_range(min(counts), max(counts), freq="M"))
    if span_months > 36:
        step = max(1, span_months // 12 // 12)  # 年の目盛りを最大12本程度に
        axis = alt.Axis(format="%Y", tickCount={"interval": "year", "step": step})
    else:
        step = max(1, span_months // 12)
        axis = alt.Axis(
            format="%Y-%m", tickCount={"interval": "month", "step": step}, labelAngle=-45
        )
    bars = alt.Chart(df).mark_bar().encode(
        # yearmonth を付けると棒が1か月分の幅になる(付けないと細い線になる)
        x=alt.X("yearmonth(month):T", title=None, axis=axis),
        y=alt.Y("count:Q", title="枚数"),
        tooltip=[alt.Tooltip("month:T", title="月", format="%Y-%m"), alt.Tooltip("count:Q", title="枚数")],
    )
    band = alt.Chart(period).mark_rect(opacity=0.2, color="orange").encode(
        x=alt.X("start:T", axis=axis), x2="end:T"
    )
    st.caption(
        f"月別の撮影枚数(フィルタ適用後、全 {len(dates):,} 枚、{dates[0]} 〜 {dates[-1]})。"
        "橙の帯が選択中の期間。"
    )
    st.altair_chart((band + bars).properties(height=200), width="stretch")


show_availability = src.asset_type == "image_collection" and src.temporal
availability_ok, availability_dates = (
    render_scene_count(st.sidebar) if show_availability else (False, None)
)

st.sidebar.header("5. 表示")
basemap = st.sidebar.selectbox(
    "背景地図", [b[0] for b in BASEMAPS],
    help="背景地図はここで一括管理します。地図右上のベース選択は表示しません。",
)
opacity = st.sidebar.slider("不透明度", 0.0, 1.0, 1.0, 0.05)
stretch = st.sidebar.checkbox(
    "自動ストレッチ", value=force_stretch, disabled=force_stretch,
    help="実データの2〜98パーセンタイルで表示レンジを決めます。"
    "値域の分からないデータセットで有効。",
)
if force_stretch:
    st.sidebar.caption("この解析は値域が事前に決まらないため自動ストレッチ必須です。")
show_working = st.sidebar.checkbox("作業中のパイプラインを表示", value=True)


# --------------------------------------------------------------- 組み立て

spec = {
    "source": src.id,
    "source_params": source_params,
    "recipe": recipe.id,
    "recipe_params": recipe_params,
    "analyses": chain,
}
label = " → ".join([src.label, recipe.label] + chain_labels)

if start_date >= end_date:
    st.warning("開始日は終了日より前にしてください。")
    st.stop()

st.title("GEE Viewer")
st.caption(label)

if "layers" not in st.session_state:
    st.session_state.layers = []

cols = st.columns([1, 1, 4])
if cols[0].button("レイヤーとして追加", type="primary"):
    st.session_state.layers.append(
        {
            "label": label,
            "spec": spec,
            "opacity": opacity,
            "stretch": stretch,
            "start": str(start_date),
            "end": str(end_date),
        }
    )
if cols[1].button("レイヤーを全消去"):
    st.session_state.layers = []


# ------------------------------------------------------------------- 地図

# tiles=None で既定の背景を付けず、背景地図も明示的に足す。こうすると
# LayerControl 上でラジオボタンとして切り替え・除去できる(既定の
# folium.Map(tiles=...) は外せない背景として焼き付いてしまう)。
m = folium.Map(location=[lat, lon], zoom_start=zoom, tiles=None)
for layer in ui.build_basemap_layers(BASEMAPS, basemap):
    folium.TileLayer(
        tiles=layer["tiles"],
        attr=layer["attr"],
        name=layer["name"],
        overlay=False,
        control=False,
        show=layer["show"],
    ).add_to(m)

errors: list[str] = []


def add_layer(name: str, layer_spec: dict, start: str, end: str,
              layer_opacity: float, layer_stretch: bool) -> None:
    try:
        url = tile_url(
            json.dumps(layer_spec, sort_keys=True),
            lon, lat, buffer_km, start, end, layer_stretch,
        )
    except Exception as exc:
        # 空コレクションを median() すると「バンドが無い」という一見関係の
        # 無いメッセージになるため、原因の当たりを付けて伝える。
        if "no bands" in str(exc):
            errors.append(
                f"{name}: 指定した期間・領域に画像がありません。"
                "期間を広げるか、雲量上限を上げてみてください。"
            )
        else:
            errors.append(f"{name}: {exc}")
        return
    folium.TileLayer(
        tiles=url, attr="Google Earth Engine", name=name,
        overlay=True, opacity=layer_opacity,
    ).add_to(m)


# 固定したレイヤーを先に、作業中のものを最後に(= 最前面に)描く
for layer in st.session_state.layers:
    add_layer(layer["label"], layer["spec"], layer["start"], layer["end"],
              layer["opacity"], layer["stretch"])
if show_working:
    add_layer(f"[作業中] {label}", spec, str(start_date), str(end_date),
              opacity, stretch)

# collapsed=False で常に開いた状態にする(背景地図のラジオと、
# 各EEレイヤーのチェックボックスがひと目で見える)
folium.LayerControl(collapsed=False).add_to(m)

for err in errors:
    st.error(f"画像を取得できませんでした。期間や領域を変えてみてください。\n\n{err}")

st_folium(m, use_container_width=True, height=620, returned_objects=[])

st.caption(
    f"期間: {start_date} 〜 {end_date} / 中心: ({lat:.4f}, {lon:.4f}) / "
    f"領域: 約{buffer_km * 2}km四方 / 解像度: {src.native_scale}m"
)

if availability_ok:
    render_monthly_chart(availability_dates)


# --------------------------------------------------------- レイヤー一覧

if st.session_state.layers:
    st.subheader("固定中のレイヤー")
    for i, layer in enumerate(st.session_state.layers):
        row = st.columns([6, 1])
        row[0].write(f"{i + 1}. {layer['label']}")
        if row[1].button("削除", key=f"del{i}"):
            st.session_state.layers.pop(i)
            st.rerun()


# --------------------------------------------------------- コード書き出し

st.subheader("コードとして書き出す")
st.write(
    "今の設定をそのまま動く Python コードにして持ち帰れます。"
    "GUI でできる範囲を超えた解析をしたくなったら、これをローカルで開いて"
    "続きを書いてください。書き出したコードはこのアプリに依存しません。"
)

export_layers = list(st.session_state.layers)
working_layer = {
    "label": label,
    "spec": spec,
    "opacity": opacity,
    "stretch": stretch,
    "start": str(start_date),
    "end": str(end_date),
}
if st.checkbox("作業中のパイプラインも含める", value=True):
    export_layers.append(working_layer)

if not export_layers:
    st.info("書き出すレイヤーがありません。レイヤーを固定するか、上のチェックを入れてください。")
else:
    st.caption("書き出し対象:\n\n" + codegen.describe(export_layers))
    args = (export_layers, lon, lat, buffer_km, zoom)
    try:
        script = codegen.generate_py(*args)
        notebook = codegen.generate_ipynb(*args)
    except Exception as e:
        st.error(f"コードを生成できませんでした: {e}")
    else:
        dl = st.columns([1, 1, 3])
        dl[0].download_button(
            "Python (.py)", script, file_name="gee_analysis.py",
            mime="text/x-python", type="primary",
        )
        dl[1].download_button(
            "Notebook (.ipynb)", notebook, file_name="gee_analysis.ipynb",
            mime="application/x-ipynb+json",
        )
        with st.expander("生成されるコードを確認する"):
            st.code(script, language="python")
