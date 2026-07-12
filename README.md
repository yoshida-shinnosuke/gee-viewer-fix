# GEE Viewer

Google Earth Engine のデータセットを取得して地図に描画する汎用ビューア。
Streamlit 製の純粋な Python アプリ(notebook 不使用)。

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

ブラウザが開き、サイドバーでデータセット・期間・領域を選ぶと地図に描画されます。

## 構成

```
app.py                 # Streamlit UI
gee_viewer/
  auth.py              # EE認証(サービスアカウント → 個人認証のフォールバック)
  catalog.py           # データセット定義。ここに追加するだけで拡張できる
requirements.txt
```

## データセットの追加方法

`gee_viewer/catalog.py` に build 関数と CATALOG エントリを追加するだけ。
`build(region, start, end, params)` が `ee.Image` を返せば何でも描画できます。

## 設計メモ

- 表示は `getMapId()` のタイルURLを folium に貼る方式。ピクセルを
  ダウンロードしないため高速で、ズーム・パンも自由。
- k-means クラスタリングと Canny 法による海岸線抽出は EE サーバー側の
  組み込み(`ee.Clusterer` / `ee.Algorithms.CannyEdgeDetector`)で実装済み。
  ピクセル取得は発生しない。
- numpy 等が必要な独自解析を将来追加する場合のみ
  `getInfo()`/`sampleRectangle()` でピクセル取得する。

## 一般公開する場合(将来)

コード変更は不要。サービスアカウントを用意して以下のいずれかを設定:

1. `.streamlit/secrets.toml` に `[ee_service_account]` セクションとして
   サービスアカウント鍵JSONの内容を記載(Streamlit Cloud はUIから設定)
2. または環境変数 `EE_SERVICE_ACCOUNT_JSON` に鍵ファイルのパスを設定

`auth.py` がサービスアカウントを優先して使用します。
個人利用時のGCPプロジェクトは環境変数 `EE_PROJECT` で変更可能(既定: `ee-naoki`)。

**注意**: 鍵ファイルや secrets.toml は絶対にコミットしないこと(.gitignore 済み)。
