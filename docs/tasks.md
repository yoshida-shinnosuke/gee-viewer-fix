# タスク一覧(未着手)

このドキュメントは「次に何をやるか」を管理する。背景の要求一覧は
[requirements.md](requirements.md)、判断に迷ったときの基準は [vision.md](vision.md)、
実装時の設計は [design.md](design.md) を参照。

優先度順。[requirements.md](requirements.md#トレース状況) で「未着手」のGEE API機能に対応する。

## 1. 地図上での領域描画(`ee.Geometry`)— 最優先

- **現状**: [app.py](../app.py) の緯度・経度・半径の数値入力と、場所名検索
  (OSM Nominatim、[geocode.py](../gee_viewer/geocode.py))。いずれも領域の形は
  「中心 + 半径の矩形」に限られ、`filterBounds()` にはそれを渡している。
- **やりたいこと**: 地図上でポリゴン/矩形を描いて `ee.Geometry` として使えるようにする。
- **影響範囲**: app.py の領域選択UI、[pipeline.py](../gee_viewer/pipeline.py) の
  `filterBounds` 呼び出し、[codegen.py](../gee_viewer/codegen.py) の書き出しコード
  (領域の表現方法が変わるため両方の追従が要る)。
- [requirements.md](requirements.md#次に埋めるべきギャップ) の「次に埋めるべきギャップ」を参照。

## 2. `reduceRegion` の時系列グラフ

- `ee.ImageCollection` に対して `reduceRegion` を時系列で実行し、グラフ表示する。
- 現状これに近い処理は自動ストレッチ(2〜98パーセンタイルの取得)のみで、
  時系列としての集計・表示は未実装。

## 3. `ee.Classifier`(教師あり分類)

- 現在は `ee.Clusterer`(教師なし, k-means)のみ対応([analyses.py](../gee_viewer/analyses.py))。
- 着手前に、トレーニングデータの与え方(地図上で描画 / アップロード)を決める必要がある。
  タスク1(領域描画)と設計が重なる可能性が高い。

## 4. `Export.image.toDrive()` 等

- 現在は `getMapId()` によるタイル表示のみで、エクスポート機能がない。
- [vision.md](vision.md) の方針4(重い処理はGEE側に置く)との整合を保ったまま、
  「結果を持ち出したい」というニーズにどう応えるかを設計する必要がある。

## 5. 自由なバンド演算式の入力

- 現在は登録済みの Recipe / Analysis からの選択のみ([recipes.py](../gee_viewer/recipes.py) /
  [analyses.py](../gee_viewer/analyses.py))。
- `ee.Image.expression()` の自由記述UIを検討。[vision.md](vision.md) の方針2
  「壊れた組み合わせを作らせない」との両立(自由記述は原理的にエラーを防げない)が課題。

## 6. EE プロジェクトIDのハードコード解消 — 着手中

- **現状**: [auth.py](../gee_viewer/auth.py) の `DEFAULT_PROJECT` が
  `os.environ.get("EE_PROJECT", "ee-naoki")` で、未設定時に作者個人の
  GCP プロジェクトIDを使う。他メンバーは `EE_PROJECT` を設定しないと動かず
  (権限エラーになる)、書き出しコードにも `ee-naoki` が混ざる。
- **やりたいこと**: 既定値を廃止し、プロジェクトIDは各自の環境変数 `EE_PROJECT` で指定する。
- **仕様**:
  - 個人認証で `EE_PROJECT` が未設定なら、初期化せずエラーで止める。画面には
    原因と解決方法(`.env` の作り方、`export`、VS Code デバッグでの読み込み)を表示する。
  - サービスアカウント認証(secrets / `EE_SERVICE_ACCOUNT_JSON`)は従来どおり。
    プロジェクトIDは資格情報から決まるため `EE_PROJECT` は不要。
  - `.env.sample` を置く(`EE_PROJECT=your-gcp-project-id`)。各自 `.env` にコピーして使う。
    `.env` は `.gitignore` に追加する。
  - VS Code のデバッグ起動([launch.json](../.vscode/launch.json))は `envFile` で `.env` を読む。
  - 書き出しコードの `ee.Initialize(project=...)` には、実行時の `EE_PROJECT` の値が入る。
- **影響範囲**: [auth.py](../gee_viewer/auth.py)(既定値の廃止・未設定エラー)、
  [app.py](../app.py)(エラー表示、`codegen` へ渡すプロジェクトID)、
  `.env.sample`(新規)、`.gitignore`、`.vscode/launch.json`、
  README(セットアップ手順)、[design.md](design.md)(auth の説明)。
- **受け入れ確認**:
  1. `EE_PROJECT` 未設定で起動 → エラーと解決方法が画面に出る
  2. `.env` を用意して起動 / デバッグ起動 → 地図が表示される
  3. 書き出したコードに `ee-naoki` が含まれない
- **未決**: 書き出しコードに利用者自身のプロジェクトIDをそのまま入れる(上記仕様)か、
  プレースホルダ(`your-gcp-project-id`)にするか。前者を暫定とする。
