# タスク一覧(未着手)

このドキュメントは「次に何をやるか」を管理する。背景の要求一覧は
[requirements.md](requirements.md)、判断に迷ったときの基準は [vision.md](vision.md)、
実装時の設計は [design.md](design.md) を参照。

優先度順。[requirements.md](requirements.md#トレース状況) で「未着手」のGEE API機能に対応する。

## 1. 地図上での領域描画(`ee.Geometry`)— 最優先

- **現状**: [app.py](../app.py) の緯度・経度・半径の数値入力(`lat` / `lon`)のみ。
  `filterBounds()` に渡す領域はここから矩形として組み立てている。
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
