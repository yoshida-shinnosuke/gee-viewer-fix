"""解析(パイプライン第3段)。0個以上を直列に繋げられる。

すべて Earth Engine のサーバー側演算だけで書かれている。OpenCV の
ぼかし・収縮膨張・エッジ検出に相当する処理は近傍演算(focal_*)と
畳み込み(convolve)で表現できるため、ピクセルをダウンロードする必要が
無い。結果はタイルURLとして返るので、どのズームでも動く。

accepts が入力画像の形(single / rgb / multi)と合うときだけ UI に現れる。
例えば Canny は単バンドにしか適用できないので、トゥルーカラーの直後には
出てこない。

各解析は実行用の `build` と書き出し用の `code` を対で持つ。
"""

from __future__ import annotations

import ee

from .pipeline import Analysis, ParamSpec

_BINARY_VIS = {"min": 0, "max": 1, "palette": ["#000000", "#ffffff"]}

_RADIUS = ParamSpec(
    "radius", "近傍半径 (ピクセル)", "int", 2, 1, 15,
    help="大きいほど強く効くが、細部が潰れる。",
)


# ------------------------------------------------------------ 教師なし分類


def _kmeans(img, params, ctx):
    """k-means クラスタリング。学習も分類も EE サーバー側で実行する。

    クラスタ番号自体に意味は無く、色の対応は実行のたびに変わりうる。
    """
    training = img.sample(
        region=ctx["region"],
        scale=ctx["scale"],
        numPixels=params.get("n_samples", 5000),
        seed=0,
        geometries=False,
    )
    clusterer = ee.Clusterer.wekaKMeans(params.get("n_clusters", 5)).train(training)
    # randomVisualizer() は viz-red/green/blue に元の cluster バンドを足した
    # 4バンドを返す。RGB として描くため先頭3バンドだけ取り出す。
    return img.cluster(clusterer).randomVisualizer().select([0, 1, 2])


def _kmeans_code(params, ctx):
    return f"""training = image.sample(
    region={ctx['region']},
    scale={ctx['scale']},
    numPixels={params.get('n_samples', 5000)},
    seed=0,
    geometries=False,
)
clusterer = ee.Clusterer.wekaKMeans({params.get('n_clusters', 5)}).train(training)
# randomVisualizer() は cluster バンドも残すので先頭3バンド(RGB)だけ使う
image = image.cluster(clusterer).randomVisualizer().select([0, 1, 2])"""


# -------------------------------------------------------------- 2値化・輪郭


def _threshold(img, params, ctx):
    v = params.get("value", 0.0)
    above = params.get("above", True)
    return (img.gt(v) if above else img.lt(v)).rename("mask")


def _threshold_code(params, ctx):
    op = "gt" if params.get("above", True) else "lt"
    return f"image = image.{op}({params.get('value', 0.0)!r}).rename('mask')"


def _canny(img, params, ctx):
    """Canny 法によるエッジ検出。2値マスクに掛けると境界線が線として出る。"""
    edges = ee.Algorithms.CannyEdgeDetector(
        image=img,
        threshold=params.get("threshold", 0.5),
        sigma=params.get("sigma", 1.0),
    )
    return edges.selfMask() if params.get("transparent", True) else edges


def _canny_code(params, ctx):
    lines = [
        "image = ee.Algorithms.CannyEdgeDetector(",
        "    image=image,",
        f"    threshold={params.get('threshold', 0.5)!r},",
        f"    sigma={params.get('sigma', 1.0)!r},",
        ")",
    ]
    if params.get("transparent", True):
        lines.append("image = image.selfMask()  # 境界以外を透明に")
    return "\n".join(lines)


def _gradient(img, params, ctx):
    """勾配強度(Sobel 相当)。x/y 方向の微分を合成した大きさ。"""
    g = img.gradient()
    return g.pow(2).reduce(ee.Reducer.sum()).sqrt().rename("gradient")


_GRADIENT_CODE = """gradient = image.gradient()
image = gradient.pow(2).reduce(ee.Reducer.sum()).sqrt().rename('gradient')"""


def _laplacian(img, params, ctx):
    """ラプラシアン畳み込み。輪郭が正負の帯として出る。"""
    return img.convolve(ee.Kernel.laplacian8()).rename("laplacian")


# ------------------------------------------------------------ 近傍フィルタ


def _focal(op: str):
    """OpenCV の blur / medianBlur / erode / dilate に相当する近傍演算。

    build と code を同じ op から作るのでズレようがない。
    """

    def build(img, params, ctx):
        return getattr(img, f"focal_{op}")(params.get("radius", 2), "square", "pixels")

    def code(params, ctx):
        return f"image = image.focal_{op}({params.get('radius', 2)}, 'square', 'pixels')"

    return build, code


def _selfmask(img, params, ctx):
    """値が 0 のピクセルを透明にする。重ね合わせ表示用。"""
    return img.selfMask()


# ------------------------------------------------------------------ 地形


def _slope(img, params, ctx):
    return ee.Terrain.slope(img).rename("slope")


def _hillshade(img, params, ctx):
    return ee.Terrain.hillshade(
        img,
        azimuth=params.get("azimuth", 315),
        elevation=params.get("elevation", 45),
    ).rename("hillshade")


def _hillshade_code(params, ctx):
    return (
        f"image = ee.Terrain.hillshade(\n"
        f"    image,\n"
        f"    azimuth={params.get('azimuth', 315)},\n"
        f"    elevation={params.get('elevation', 45)},\n"
        f").rename('hillshade')"
    )


def _focal_analysis(rid: str, label: str, op: str, description: str) -> Analysis:
    build, code = _focal(op)
    return Analysis(
        id=rid, label=label, build=build, code=code,
        accepts="any", produces="same", params=(_RADIUS,), description=description,
    )


ANALYSES: tuple[Analysis, ...] = (
    Analysis(
        id="kmeans",
        label="k-means クラスタリング",
        build=_kmeans,
        code=_kmeans_code,
        accepts="any",
        produces="rgb",
        vis={"min": 0, "max": 255},
        params=(
            ParamSpec("n_clusters", "クラスタ数", "int", 5, 2, 20),
            ParamSpec("n_samples", "学習サンプル数", "int", 5000, 500, 20000, 500,
                      help="多いほど安定するが計算時間が伸びる。"),
        ),
        description="教師なし分類。入力の全バンドを使って領域を塗り分ける(色はランダム)。",
    ),
    Analysis(
        id="threshold",
        label="閾値で2値化",
        build=_threshold,
        code=_threshold_code,
        accepts="single",
        produces="single",
        vis=_BINARY_VIS,
        params=(
            ParamSpec("value", "閾値", "float", 0.0, -1.0, 1.0, 0.01),
            ParamSpec("above", "閾値より大きい側を 1 にする", "bool", True),
        ),
        description="指数を白黒のマスクに変える。輪郭抽出や面積計算の前段。",
    ),
    Analysis(
        id="canny",
        label="Canny エッジ検出",
        build=_canny,
        code=_canny_code,
        accepts="single",
        produces="single",
        vis={"min": 0, "max": 1, "palette": ["#ff0000"]},
        params=(
            ParamSpec("threshold", "感度", "float", 0.5, 0.05, 2.0, 0.05),
            ParamSpec("sigma", "平滑化 σ", "float", 1.0, 0.0, 5.0, 0.5),
            ParamSpec("transparent", "境界以外を透明にする", "bool", True,
                      help="他のレイヤーに重ねる場合はオン。"),
        ),
        description="境界を線として抽出する。2値マスクの後段に置くのが定石。",
    ),
    Analysis(
        id="gradient",
        label="勾配強度 (Sobel相当)",
        build=_gradient,
        code=lambda p, ctx: _GRADIENT_CODE,
        accepts="single",
        produces="single",
        vis={"min": 0, "max": 1},
        needs_stretch=True,
        description="値の変化が急な場所ほど明るくなる。表示レンジは自動ストレッチで決まる。",
    ),
    Analysis(
        id="laplacian",
        label="ラプラシアン",
        build=_laplacian,
        code=lambda p, ctx: (
            "image = image.convolve(ee.Kernel.laplacian8()).rename('laplacian')"
        ),
        accepts="single",
        produces="single",
        vis={"min": -1, "max": 1},
        needs_stretch=True,
        description="2次微分による輪郭強調。表示レンジは自動ストレッチで決まる。",
    ),
    _focal_analysis("focal_median", "メディアン平滑化", "median",
                    "ごま塩ノイズを消す。OpenCV の medianBlur 相当。"),
    _focal_analysis("focal_mean", "平均平滑化 (ぼかし)", "mean",
                    "全体をなめらかにする。OpenCV の blur 相当。"),
    _focal_analysis("focal_min", "収縮 (erode)", "min",
                    "明るい領域を痩せさせる。2値マスクの小さな島を消すのに使う。"),
    _focal_analysis("focal_max", "膨張 (dilate)", "max",
                    "明るい領域を太らせる。収縮と組み合わせて穴埋め・ノイズ除去。"),
    Analysis(
        id="selfmask",
        label="0 を透明にする",
        build=_selfmask,
        code=lambda p, ctx: "image = image.selfMask()",
        accepts="any",
        produces="same",
        description="値が 0 の場所を抜く。抽出結果を他のレイヤーに重ねるとき用。",
    ),
    Analysis(
        id="slope",
        label="傾斜 (度)",
        build=_slope,
        code=lambda p, ctx: "image = ee.Terrain.slope(image).rename('slope')",
        accepts="single",
        produces="single",
        vis={"min": 0, "max": 45,
             "palette": ["#ffffff", "#ffff00", "#ff8000", "#ff0000"]},
        description="標高から傾斜角を計算する。DEM に対して使う。",
    ),
    Analysis(
        id="hillshade",
        label="陰影起伏",
        build=_hillshade,
        code=_hillshade_code,
        accepts="single",
        produces="single",
        vis={"min": 0, "max": 255},
        params=(
            ParamSpec("azimuth", "光源方位 (度)", "int", 315, 0, 360, 15),
            ParamSpec("elevation", "光源高度 (度)", "int", 45, 0, 90, 5),
        ),
        description="標高に陰影を付けて地形を立体的に見せる。DEM に対して使う。",
    ),
)

BY_ID: dict[str, Analysis] = {a.id: a for a in ANALYSES}
