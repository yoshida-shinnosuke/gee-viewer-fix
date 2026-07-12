"""データセットカタログ。

汎用ビューアの中核。ここに辞書を追加するだけで新しいデータセット/
可視化をアプリに追加できる。

各エントリの構造:
    label:       UIに表示する名前
    build:       (region, start, end, params) -> ee.Image を返す関数
    vis:         getMapId に渡す可視化パラメータ
    description: UIに表示する説明
    needs_cloud_filter: 雲量スライダーを表示するか
    needs_cluster_count: (任意) クラスタ数スライダーを表示するか
"""

import ee


# ---------------------------------------------------------------- builders


def _s2_collection(region, start, end, max_cloud):
    return (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(region)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", max_cloud))
    )


def s2_true_color(region, start, end, params):
    col = _s2_collection(region, start, end, params.get("max_cloud", 70))
    return col.median().select(["B4", "B3", "B2"])


def s2_false_color(region, start, end, params):
    """偽色 (NIR, Red, Green) — 植生が赤く見える。"""
    col = _s2_collection(region, start, end, params.get("max_cloud", 70))
    return col.median().select(["B8", "B4", "B3"])


def s2_ndwi(region, start, end, params):
    """NDWI = (Green - NIR) / (Green + NIR)。水域抽出用。"""
    col = _s2_collection(region, start, end, params.get("max_cloud", 70))
    img = col.median()
    return img.normalizedDifference(["B3", "B8"]).rename("NDWI")


def s2_ndvi(region, start, end, params):
    col = _s2_collection(region, start, end, params.get("max_cloud", 70))
    img = col.median()
    return img.normalizedDifference(["B8", "B4"]).rename("NDVI")


def s1_vv(region, start, end, params):
    """Sentinel-1 SAR (VV偏波, 下降軌道)。"""
    col = (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(region)
        .filterDate(start, end)
        .filter(ee.Filter.eq("orbitProperties_pass", "DESCENDING"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .select("VV")
    )
    return col.mean()


def copernicus_dem(region, start, end, params):
    return ee.ImageCollection("COPERNICUS/DEM/GLO30").select("DEM").mosaic()


def s2_kmeans(region, start, end, params):
    """Sentinel-2 合成画像の k-means クラスタリング (教師なし分類)。

    学習も分類も EE サーバー側で実行するため、ピクセルはダウンロード
    しない。クラスタ番号自体に意味はなく、色の対応は実行ごとに変わりうる。
    """
    col = _s2_collection(region, start, end, params.get("max_cloud", 70))
    img = col.median().select(["B2", "B3", "B4", "B8"])
    training = img.sample(region=region, scale=10, numPixels=5000, seed=0)
    clusterer = ee.Clusterer.wekaKMeans(params.get("n_clusters", 5)).train(training)
    return img.cluster(clusterer).randomVisualizer()


def s2_coastline_canny(region, start, end, params):
    """NDWI を2値化した水域マスクに Canny 法を適用し、水陸境界線を抽出。

    境界線以外は selfMask() で透明にするので、トゥルーカラー等に
    重ねて使う想定。
    """
    col = _s2_collection(region, start, end, params.get("max_cloud", 70))
    ndwi = col.median().normalizedDifference(["B3", "B8"])
    water = ndwi.gt(0)
    edges = ee.Algorithms.CannyEdgeDetector(image=water, threshold=0.5, sigma=1)
    return edges.selfMask()


# ------------------------------------------------------------------ catalog

CATALOG = {
    "s2_true_color": {
        "label": "Sentinel-2 トゥルーカラー",
        "build": s2_true_color,
        "vis": {"min": 0, "max": 3000},
        "description": "光学画像 (B4/B3/B2)。期間中の中央値合成。",
        "needs_cloud_filter": True,
    },
    "s2_false_color": {
        "label": "Sentinel-2 フォールスカラー",
        "build": s2_false_color,
        "vis": {"min": 0, "max": 3000},
        "description": "NIR/Red/Green 合成。植生が赤く表示される。",
        "needs_cloud_filter": True,
    },
    "s2_ndwi": {
        "label": "Sentinel-2 NDWI (水域指数)",
        "build": s2_ndwi,
        "vis": {"min": -1, "max": 1, "palette": ["brown", "white", "blue"]},
        "description": "(Green−NIR)/(Green+NIR)。青いほど水域。海岸線抽出の元データ。",
        "needs_cloud_filter": True,
    },
    "s2_ndvi": {
        "label": "Sentinel-2 NDVI (植生指数)",
        "build": s2_ndvi,
        "vis": {"min": -1, "max": 1, "palette": ["blue", "white", "green"]},
        "description": "(NIR−Red)/(NIR+Red)。緑が濃いほど植生が活発。",
        "needs_cloud_filter": True,
    },
    "s1_vv": {
        "label": "Sentinel-1 SAR (VV)",
        "build": s1_vv,
        "vis": {"min": -25, "max": 0},
        "description": "レーダー後方散乱 (dB)。雲を透過。水面は暗く写る。",
        "needs_cloud_filter": False,
    },
    "dem": {
        "label": "Copernicus DEM (標高)",
        "build": copernicus_dem,
        "vis": {"min": 0, "max": 1500, "palette": ["green", "yellow", "brown", "white"]},
        "description": "30m解像度の全球標高モデル。期間指定は無視される。",
        "needs_cloud_filter": False,
    },
    "s2_kmeans": {
        "label": "Sentinel-2 k-means クラスタリング",
        "build": s2_kmeans,
        "vis": {},  # randomVisualizer() がRGB画像を返すので可視化パラメータ不要
        "description": "教師なし分類。B2/B3/B4/B8 を指定クラスタ数で塗り分ける(色はランダム)。",
        "needs_cloud_filter": True,
        "needs_cluster_count": True,
    },
    "s2_coastline": {
        "label": "海岸線抽出 (NDWI + Canny)",
        "build": s2_coastline_canny,
        "vis": {"min": 0, "max": 1, "palette": ["red"]},
        "description": "NDWI の水陸境界を Canny 法で線として抽出。境界以外は透明なので重ね合わせ推奨。",
        "needs_cloud_filter": True,
    },
}
