# GEE Viewer

**衛星データ解析を、コードを書けない人にも届ける。**

Google Earth Engine (GEE) は強力だが、公式の Code Editor は JavaScript が
書けないと使えない。このアプリは GUI の操作だけで GEE の解析を完結できる
状態を目指している。目的と方針の詳細は **[docs/vision.md](docs/vision.md)** を参照。

Streamlit 製の純粋な Python アプリ(notebook 不使用)。

サイドバーを上から下へ辿ると、1本の処理パイプラインが決まる。

```text
データセット → バンド合成・指数 → 解析(0段以上) → 可視化
```

例: `Sentinel-2 → NDWI → 閾値で2値化 → Canny エッジ検出` で海岸線を抽出し、
`Sentinel-2 → トゥルーカラー` に重ねる。

## セットアップ

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
earthengine authenticate   # 初回のみ(個人利用)
```

## 起動

```bash
streamlit run app.py
```

## 使えるデータセット

2つの経路がある。

1. **よく使う** — 手書きで常設した17件(Sentinel-2/1、Landsat 5〜9、MODIS、
   各種 DEM、土地被覆、夜間光ほか)。バンドの役割が確定しているので、
   指数も解析もすべて使える。
2. **全カタログを辿る / IDを直接入力** — GEE の STAC カタログ(1,000件超)から
   メタデータを取得して自動設定する。提供元 → データセットの2段で辿れる。

2 の経路でも、STAC の中心波長情報から `red` / `nir` / `swir1` などの役割を
自動推定するため、光学センサなら NDVI やトゥルーカラーがそのまま使える。
役割を推定できないデータセット(SAR・土地被覆・気象など)でも、単バンド表示と
クラスタリング・近傍フィルタ系の解析は使える。

## 選択肢は自動的に絞り込まれる

各段が「何を要求し、何を出すか」を宣言しているので、UI には上流と
相性の合う選択肢しか出ない。

- Sentinel-1(SAR)には `red` / `nir` が無い → トゥルーカラーも NDVI も現れない
- Canny エッジ検出は単バンド専用 → トゥルーカラーの直後には現れない
- DEM は時間軸を持たない → 期間の入力欄自体が消える
- データのある期間が STAC から分かる → 日付の初期値がその範囲に収まる

## 地図の操作

地図右上のレイヤー選択(常時展開)で、次の2つを切り替えられる。

- **背景地図**（ラジオ選択）— OpenStreetMap / 淡色 / 暗色 / 衛星画像(Esri) / **背景なし**。
  抽出結果だけを見たいときは「背景なし」を選ぶ。サイドバーの「背景地図」でも初期値を指定できる。
- **解析レイヤー**（チェックボックス）— 固定したレイヤーと作業中のパイプラインを個別に表示・非表示。

## コードとして持ち帰る

画面下部の「コードとして書き出す」から、今の設定をそのまま動く Python コードとして
ダウンロードできる。GUI でできる範囲を超えた解析をしたくなったら、これをローカルで
開いて続きを書けばよい。

- **`.py`** — folium で地図HTML(`map.html`)を書き出すスクリプト
- **`.ipynb`** — geemap で対話的に表示するノートブック(`Map.addLayer` は
  GEE Code Editor と同じ書き味)

固定中のレイヤーと作業中のパイプラインをまとめて書き出せる。
**生成されたコードはこのアプリに依存しない**(`earthengine-api` と表示ライブラリだけで動く)。
画面に出ている絵と書き出したコードが同じ計算をすることは、Earth Engine の
`serialize()` を突き合わせるテストで検証している。

## 拡張のしかた

追記だけで完結し、[app.py](app.py) の変更は不要。パラメータのスライダーも
`ParamSpec` の宣言から自動生成される。

| やりたいこと | 追記先 |
| --- | --- |
| データセットを常設したい | [gee_viewer/sources.py](gee_viewer/sources.py) の `CURATED` |
| 新しい指数・合成を足したい | [gee_viewer/recipes.py](gee_viewer/recipes.py) の `RECIPES` |
| 新しい解析を足したい | [gee_viewer/analyses.py](gee_viewer/analyses.py) の `ANALYSES` |

指数は衛星ごとに書く必要がない。`Source` の段でバンド名が役割名
(`red`, `nir`, …)に正規化され、値も反射率 0–1 に揃えられるため、

```python
img.normalizedDifference(["nir", "red"])
```

と1回書けば Sentinel-2 でも Landsat でも MODIS でも動く。

## 構成

```text
app.py                 # Streamlit UI・パイプラインの組み立て
gee_viewer/
  pipeline.py          # 型定義と合成ロジック(中核)
  sources.py           # データセット定義
  recipes.py           # バンド合成・指数
  analyses.py          # 解析
  stac.py              # STACから Source を自動生成
  codegen.py           # .py / .ipynb の書き出し
  ui.py                # ParamSpec → ウィジェット
  auth.py              # EE認証
docs/
  vision.md            # 目的と方針(なぜ作るか)
  requirements.md      # 要求一覧・ロードマップ(何を作るか)
  design.md            # 設計書(どう作るか・Mermaid図つき)
  tasks.md             # 未着手タスクの一覧
```

## 設計メモ

- 表示は `getMapId()` のタイルURLを folium に貼る方式。**ピクセルを
  ダウンロードしない**ため高速で、ズーム・パンも自由。
- OpenCV 相当の処理(ぼかし・収縮膨張・エッジ検出)も、近傍演算 `focal_*` と
  `ee.Algorithms.CannyEdgeDetector`、畳み込みで EE サーバー側に落としてある。
  そのため上記の原則を崩していない。
- 自動ストレッチは統計値(2〜98パーセンタイル)だけを取得する。画像は取得しない。
  勾配・ラプラシアンのように値域が事前に決まらない解析では自動的に有効化される。
- numpy/cv2 が必要な独自解析を将来入れる場合のみ、`sampleRectangle()` 等で
  ピクセルを取得することになる(領域サイズとズームに制約が付く)。

詳細は [docs/design.md](docs/design.md) を参照。
なぜこの設計なのかは [docs/vision.md](docs/vision.md) に目的と紐づけて記録している。
何を作るか(要求一覧・ロードマップ)は [docs/requirements.md](docs/requirements.md)、
未着手タスクは [docs/tasks.md](docs/tasks.md) を参照。

## 未対応(次にやりたいこと)

最優先は **地図上での領域描画**。現状は緯度経度と半径の数値入力しかなく、
「この範囲を見たい」という最も自然な操作が GUI でできていない。
ほか、時系列グラフ、教師あり分類、Export など。進捗は
[docs/requirements.md](docs/requirements.md) を参照。

## 一般公開する場合(将来)

コード変更は不要。サービスアカウントを用意して以下のいずれかを設定:

1. `.streamlit/secrets.toml` に `[ee_service_account]` セクションとして
   サービスアカウント鍵JSONの内容を記載(Streamlit Cloud はUIから設定)
2. または環境変数 `EE_SERVICE_ACCOUNT_JSON` に鍵ファイルのパスを設定

[gee_viewer/auth.py](gee_viewer/auth.py) がサービスアカウントを優先して使用する。
個人利用時のGCPプロジェクトは環境変数 `EE_PROJECT` で変更可能(既定: `ee-naoki`)。

**注意**: 鍵ファイルや secrets.toml は絶対にコミットしないこと(.gitignore 済み)。
