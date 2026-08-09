# 要求一覧(GEE API トレース状況)

このドキュメントは「何を作るか」を GEE API 単位で管理する要求一覧。
なぜ作るか・判断に迷ったときの基準は [vision.md](vision.md)、
どう作っているかは [design.md](design.md)、
未着手分のタスク分解は [tasks.md](tasks.md) を参照。

管理方針は [vision.md](vision.md) の方針1「GEE API をトレースする」に従う。
GEE API が提供する操作をひとつずつ GUI の選択肢に対応付けていき、
このアプリのロードマップを兼ねる。

## トレース状況

| GEE API / 機能 | GUI での対応 | 状況 |
| --- | --- | --- |
| `ee.ImageCollection(id)` | データセット選択(常設17件 + STAC全件) | 対応済 |
| `ee.Image(id)` | 同上(単一Image も判別して扱う) | 対応済 |
| `.filterDate()` | 期間の指定(提供期間から初期値を自動補正) | 対応済 |
| `.filterBounds()` | 中心座標 + 領域サイズ(中心は場所名検索でも指定可 / OSM Nominatim) | 対応済 |
| `.filter(ee.Filter...)` | 雲量上限、軌道方向 | 一部 |
| `.map(雲マスク)` | 雲マスクのオン/オフ(S2 / Landsat / MODIS) | 一部 |
| `.median()` `.mean()` 等 | 期間の合成方法(6種) | 対応済 |
| `.normalizedDifference()` `.expression()` | 指数・バンド合成(19種) | 一部 |
| `.select()` | 単バンド表示・全バンド | 対応済 |
| `ee.Clusterer` (教師なし分類) | k-means | 一部 |
| `ee.Algorithms.CannyEdgeDetector` | Canny エッジ検出 | 対応済 |
| `.focal_*()` `.convolve()` | 平滑化・収縮膨張・勾配・ラプラシアン | 一部 |
| `ee.Terrain` | 傾斜・陰影起伏 | 一部 |
| `Map.addLayer(img, vis, name)` | レイヤー追加・重ね合わせ・不透明度 | 対応済 |
| 可視化パラメータ | 配色・レンジ・自動ストレッチ | 対応済 |
| **コード書き出し (.py / .ipynb)** | 現在の全レイヤーをコードとして書き出し | **対応済** |
| `ee.Geometry` を地図上で描く | — | 未着手 |
| `reduceRegion` の時系列グラフ | — | 未着手 |
| `ee.Classifier` (教師あり分類) | — | 未着手 |
| `Export.image.toDrive()` 等 | — | 未着手 |
| 自由なバンド演算式の入力 | — | 未着手 |

コード書き出しが「画面の絵」とズレないための実装は
[design.md の「コード書き出し」](design.md#コード書き出し)を参照。

## 次に埋めるべきギャップ

最優先: **地図上での領域描画(`ee.Geometry`)**。現状は緯度経度と半径の数値入力
しかなく、「この範囲を見たい」という最も自然な操作が GUI でできない。
GUI で完結させるという [vision.md](vision.md) の目的に対して、
ここが一番大きな穴になっている。

上記「未着手」の各行をタスクとして分解したものは [tasks.md](tasks.md) を参照。
