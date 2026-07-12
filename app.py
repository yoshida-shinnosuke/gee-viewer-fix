"""GEE Viewer — Google Earth Engine 汎用ビューア (Streamlit).

起動: streamlit run app.py
"""

from datetime import date, timedelta

import ee
import streamlit as st

from gee_viewer import auth
from gee_viewer.catalog import CATALOG

st.set_page_config(page_title="GEE Viewer", page_icon="🌍", layout="wide")


# ---------------------------------------------------------------- EE 初期化


@st.cache_resource
def init_ee() -> str:
    """EE初期化は重いのでプロセスで1回だけ実行。"""
    return auth.initialize()


try:
    auth_mode = init_ee()
except Exception as e:
    st.error(
        "Earth Engine の初期化に失敗しました。ローカル利用の場合は "
        "`earthengine authenticate` を先に実行してください。\n\n"
        f"詳細: {e}"
    )
    st.stop()


# ------------------------------------------------------------------ sidebar

st.sidebar.title("🌍 GEE Viewer")
st.sidebar.caption(f"認証方式: {auth_mode}")

dataset_keys = st.sidebar.multiselect(
    "データセット (複数選ぶと重ね合わせ)",
    options=list(CATALOG.keys()),
    default=list(CATALOG.keys())[:1],
    format_func=lambda k: CATALOG[k]["label"],
)
if not dataset_keys:
    st.info("データセットを1つ以上選んでください。")
    st.stop()
for key in dataset_keys:
    st.sidebar.caption(f"{CATALOG[key]['label']} — {CATALOG[key]['description']}")

st.sidebar.subheader("期間")
default_end = date.today()
default_start = default_end - timedelta(days=90)
start_date = st.sidebar.date_input("開始日", value=default_start)
end_date = st.sidebar.date_input("終了日", value=default_end)

selected_entries = [CATALOG[k] for k in dataset_keys]
params: dict = {}
if any(e["needs_cloud_filter"] for e in selected_entries):
    params["max_cloud"] = st.sidebar.slider("雲量上限 (%)", 0, 100, 70)
if any(e.get("needs_cluster_count") for e in selected_entries):
    params["n_clusters"] = st.sidebar.slider("クラスタ数", 2, 10, 5)

st.sidebar.subheader("表示領域")
st.sidebar.caption("初期値は鳥取砂丘。数値を変えると移動します。")
lat = st.sidebar.number_input("緯度", value=35.545, format="%.4f")
lon = st.sidebar.number_input("経度", value=134.225, format="%.4f")
buffer_km = st.sidebar.slider("領域サイズ (km)", 1, 50, 5)
zoom = st.sidebar.slider("ズーム", 8, 16, 13)

opacity = st.sidebar.slider("レイヤー不透明度", 0.0, 1.0, 1.0, 0.05)


# -------------------------------------------------------------------- main

st.title(" + ".join(CATALOG[k]["label"] for k in dataset_keys))

if start_date >= end_date:
    st.warning("開始日は終了日より前にしてください。")
    st.stop()


@st.cache_data(show_spinner="Earth Engine からタイルURLを取得中...")
def get_tile_url(dataset_key: str, start: str, end: str,
                 lon: float, lat: float, buffer_km: int,
                 params_tuple: tuple) -> str:
    """ee.Image からタイルURLを取得 (結果はキャッシュ)。

    ピクセルをダウンロードせず getMapId のタイルURLを使うことで、
    転送量ほぼゼロでズーム・パン可能な表示を実現する。
    """
    entry = CATALOG[dataset_key]
    region = ee.Geometry.Point([lon, lat]).buffer(buffer_km * 1000).bounds()
    image = entry["build"](region, start, end, dict(params_tuple))
    map_id = ee.Image(image).clip(region).getMapId(entry["vis"])
    return map_id["tile_fetcher"].url_format


try:
    tile_urls = {
        key: get_tile_url(
            key,
            str(start_date),
            str(end_date),
            lon,
            lat,
            buffer_km,
            tuple(sorted(params.items())),
        )
        for key in dataset_keys
    }
except Exception as e:
    st.error(f"画像の取得に失敗しました。期間や領域を変えてみてください。\n\n詳細: {e}")
    st.stop()

import folium
from streamlit_folium import st_folium

m = folium.Map(location=[lat, lon], zoom_start=zoom, tiles="OpenStreetMap")
for key in dataset_keys:  # 選択順に追加 = 後に選んだレイヤーほど上に描画される
    folium.TileLayer(
        tiles=tile_urls[key],
        attr="Google Earth Engine",
        name=CATALOG[key]["label"],
        overlay=True,
        opacity=opacity,
    ).add_to(m)
folium.LayerControl().add_to(m)

st_folium(m, use_container_width=True, height=650, returned_objects=[])

st.caption(
    f"期間: {start_date} 〜 {end_date} / "
    f"中心: ({lat:.4f}, {lon:.4f}) / 領域: 約{buffer_km * 2}km四方"
)
