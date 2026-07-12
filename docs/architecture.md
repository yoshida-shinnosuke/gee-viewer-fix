# アーキテクチャ

現状のプログラム構成の設計書。図は Mermaid で記述しており、GitHub 上でそのまま描画される。

## モジュール構成

全体で約300行・3モジュール。クラスは使わず、関数と辞書で構成されている。

```mermaid
flowchart TB
    subgraph アプリ本体
        app["app.py<br/>Streamlit UI・メインフロー"]
    end

    subgraph gee_viewer パッケージ
        auth["auth.py<br/>EE認証<br/>(サービスアカウント→個人の順で試行)"]
        catalog["catalog.py<br/>データセットカタログ<br/>(拡張ポイント)"]
    end

    subgraph 外部
        ee_api["Earth Engine API<br/>(ee ライブラリ)"]
        folium_lib["folium /<br/>streamlit-folium"]
        browser["ブラウザ<br/>(地図タイル表示)"]
    end

    app -->|"initialize()"| auth
    app -->|"CATALOG 参照"| catalog
    auth -->|"ee.Initialize()"| ee_api
    catalog -->|"ImageCollection 構築"| ee_api
    app -->|"getMapId() → タイルURL"| ee_api
    app -->|"タイルURLを地図に貼る"| folium_lib
    folium_lib --> browser
    browser -.->|"タイル画像を直接取得<br/>(アプリを経由しない)"| ee_api
```

### 各モジュールの責務

| モジュール | 責務 |
| --- | --- |
| [app.py](../app.py) | Streamlit の UI(サイドバー入力)、EE 初期化の呼び出し、タイルURL取得、folium 地図の描画。状態を持たず、入力が変わるたびに上から再実行される(Streamlit の実行モデル) |
| [gee_viewer/auth.py](../gee_viewer/auth.py) | EE 認証。サービスアカウント(Streamlit secrets → 環境変数の順)を探し、なければ個人認証にフォールバック。将来の一般公開時もコード変更不要 |
| [gee_viewer/catalog.py](../gee_viewer/catalog.py) | データセット定義の集約。`CATALOG` 辞書がアプリ唯一の拡張ポイント |

## 処理フロー

ユーザーがサイドバーを操作するたびに Streamlit がスクリプト全体を再実行する。
重い処理は 2 つのキャッシュで 1 回だけ実行される。

```mermaid
sequenceDiagram
    actor User as ユーザー
    participant App as app.py
    participant Auth as auth.py
    participant Cat as catalog.py
    participant EE as Earth Engine
    participant Map as folium/ブラウザ

    User->>App: サイドバー操作(データセット・期間・領域)
    App->>Auth: initialize() ※@st.cache_resource で初回のみ
    Auth->>EE: ee.Initialize(認証情報)
    App->>Cat: CATALOG[dataset_key] を取得
    App->>Cat: entry["build"](region, start, end, params)
    Cat->>EE: ImageCollection をフィルタ・合成(遅延評価)
    App->>EE: getMapId(vis) ※@st.cache_data でキャッシュ
    EE-->>App: タイルURL
    App->>Map: TileLayer(タイルURL) を追加
    Map->>EE: 表示範囲のタイル画像を直接リクエスト
    EE-->>User: 地図表示(ズーム・パン自由)
```

設計上のポイント:

- **ピクセルを一切ダウンロードしない。** `getMapId()` のタイルURLを folium に渡すだけなので、
  転送量はほぼゼロで、ズーム・パンはブラウザと EE 間で完結する。
- **キャッシュの二層構造。** EE 初期化は `@st.cache_resource`(プロセスで1回)、
  タイルURL取得は `@st.cache_data`(入力パラメータの組が同じなら再利用)。

## 拡張ポイント: CATALOG エントリ

新しいデータセット・可視化の追加は `catalog.py` への追記だけで完結し、`app.py` の変更は不要。

```mermaid
classDiagram
    class CATALOGエントリ {
        label: UIに表示する名前
        build: (region, start, end, params) → ee.Image
        vis: getMapId に渡す可視化パラメータ
        description: UIに表示する説明
        needs_cloud_filter: 雲量スライダーの有無
        needs_cluster_count: (任意) クラスタ数スライダーの有無
    }
```

現在の登録済みエントリ(8件):

| キー | 内容 | ソース |
| --- | --- | --- |
| `s2_true_color` | トゥルーカラー(B4/B3/B2 中央値合成) | Sentinel-2 |
| `s2_false_color` | フォールスカラー(NIR/Red/Green) | Sentinel-2 |
| `s2_ndwi` | 水域指数 NDWI | Sentinel-2 |
| `s2_ndvi` | 植生指数 NDVI | Sentinel-2 |
| `s1_vv` | SAR 後方散乱(VV・下降軌道・平均) | Sentinel-1 |
| `dem` | 標高(30m 全球 DEM、期間指定は無視) | Copernicus DEM |
| `s2_kmeans` | k-means 教師なし分類(学習・分類とも EE サーバー側) | Sentinel-2 |
| `s2_coastline` | 海岸線抽出(NDWI 2値化 + Canny 法、重ね合わせ用) | Sentinel-2 |

Sentinel-2 系 4 件は共通の `_s2_collection()`(領域・期間・雲量フィルタ)を土台にしている。
