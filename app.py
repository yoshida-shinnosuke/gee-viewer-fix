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

dataset_key = st.sidebar.selectbox(
    "データセット",
    options=list(CATALOG.keys()),
    format_func=lambda k: CATALOG[k]["label"],
)
entry = CATALOG[dataset_key]
st.sidebar.caption(entry["description"])

st.sidebar.subheader("期間")
default_end = date.today()
default_start = default_end - timedelta(days=90)
start_date = st.sidebar.date_input("開始日", value=default_start)
end_date = st.sidebar.date_input("終了日", value=default_end)

params: dict = {}
if entry["needs_cloud_filter"]:
    params["max_cloud"] = st.sidebar.slider("雲量上限 (%)", 0, 100, 70)

st.sidebar.subheader("表示領域")
st.sidebar.caption("初期値は鳥取砂丘。数値を変えると移動します。")
lat = st.sidebar.number_input("緯度", value=35.545, format="%.4f")
lon = st.sidebar.number_input("経度", value=134.225, format="%.4f")
buffer_km = st.sidebar.slider("領域サイズ (km)", 1, 50, 5)
zoom = st.sidebar.slider("ズーム", 8, 16, 13)

opacity = st.sidebar.slider("レイヤー不透明度", 0.0, 1.0, 1.0, 0.05)


# -------------------------------------------------------------------- main

st.title(entry["label"])

if start_date >= end_date:
    st.warning("開始日は終了日より前にしてください。")
    st.stop()

region = ee.Geometry.Point([lon, lat]).buffer(buffer_km * 1000).bounds()


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
    tile_url = get_tile_url(
        dataset_key,
        str(start_date),
        str(end_date),
        lon,
        lat,
        buffer_km,
        tuple(sorted(params.items())),
    )
except Exception as e:
    st.error(f"画像の取得に失敗しました。期間や領域を変えてみてください。\n\n詳細: {e}")
    st.stop()

import folium
from streamlit_folium import st_folium

m = folium.Map(location=[lat, lon], zoom_start=zoom, tiles="OpenStreetMap")
folium.TileLayer(
    tiles=tile_url,
    attr="Google Earth Engine",
    name=entry["label"],
    overlay=True,
    opacity=opacity,
).add_to(m)
folium.LayerControl().add_to(m)

st_folium(m, use_container_width=True, height=650, returned_objects=[])

st.caption(
    f"期間: {start_date} 〜 {end_date} / "
    f"中心: ({lat:.4f}, {lon:.4f}) / 領域: 約{buffer_km * 2}km四方"
)
