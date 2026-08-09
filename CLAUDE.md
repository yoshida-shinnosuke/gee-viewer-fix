# CLAUDE.md

開発プロセスは [CONTRIBUTING.md](CONTRIBUTING.md) に従う。要点:

- **main へ直接 push しない。** `feature/YYYYMMDD_<主題>` ブランチを切り、PR でマージする。
- **実装より先に仕様へ反映する(SDD)。** 新機能は着手前に
  [docs/requirements.md](docs/requirements.md) と [docs/tasks.md](docs/tasks.md) を更新し、
  ユーザーの承認を得てから実装する(誤字修正などの軽微な変更は省略可)。
- **機能 PR にはドキュメント追従を含める。** requirements のトレース表・
  [docs/design.md](docs/design.md)・README・tasks.md を同じ PR で更新する。
- **判断に迷ったら [docs/vision.md](docs/vision.md) の目的に照らす。**
  UX・優先度・破壊的変更など答えが分かれる判断はユーザーに確認する。
- **セッションの終わりに宿題を issue へ書き留める。** 積み残し・作業中に見つけた
  課題があれば、セッション終了前に `gh issue create` での起票をユーザーに提案する
  (issue は宿題帳。詳細は CONTRIBUTING.md)。

## プロジェクト概要

Streamlit 製の GEE(Google Earth Engine)汎用ビューア。
目的は docs/vision.md、設計は docs/design.md を参照。
ドキュメントとコメントは日本語で書く。

## 起動・動作確認

```bash
source venv/bin/activate
streamlit run app.py
```

ユーザーが自分のインスタンスをポート 8501 で起動していることが多い。
検証用に起動する場合は別ポートを使う(例: `--server.port 8511`)。
