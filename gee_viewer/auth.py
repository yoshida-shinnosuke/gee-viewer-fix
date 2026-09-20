"""Earth Engine 認証モジュール。

認証の優先順位:
1. サービスアカウント (一般公開デプロイ用)
   - Streamlit secrets の [ee_service_account] セクション、または
   - 環境変数 EE_SERVICE_ACCOUNT_JSON (鍵ファイルへのパス)
2. 個人アカウント (ローカル/チーム利用)
   - `earthengine authenticate` 済みの資格情報を使用
   - GCP プロジェクトは環境変数 EE_PROJECT で指定する(既定値は無い)

将来 Google との交渉でアプリを一般公開できることになったら、
サービスアカウントの鍵を .streamlit/secrets.toml に置くだけで
コード変更なしで切り替わる設計。
"""

import json
import os

import ee

PROJECT_ENV = "EE_PROJECT"


class ProjectNotSetError(RuntimeError):
    """個人認証で使う GCP プロジェクトID(環境変数 EE_PROJECT)が未設定。

    メッセージは画面にそのまま出せる形(解決方法つき)で組み立てる。
    """

    def __init__(self) -> None:
        super().__init__(
            f"環境変数 `{PROJECT_ENV}` が設定されていません。"
            "Earth Engine を使う自分の GCP プロジェクトIDを指定してください。\n\n"
            "1. `.env.sample` を `.env` にコピーし、`EE_PROJECT=` の値を自分のIDにする\n"
            "2. 環境変数として読み込んでから起動する\n"
            "   - ターミナル: `set -a; source .env; set +a` のあと `streamlit run app.py`\n"
            "   - VS Code デバッグ: `.env` を置けば launch.json が自動で読み込む\n\n"
            "プロジェクトIDは https://console.cloud.google.com/ で確認できます。"
        )


def _try_service_account() -> ee.ServiceAccountCredentials | None:
    """サービスアカウント資格情報を探す。見つからなければ None。"""
    # 1) Streamlit secrets
    try:
        import streamlit as st

        if "ee_service_account" in st.secrets:
            sa = dict(st.secrets["ee_service_account"])
            return ee.ServiceAccountCredentials(
                sa["client_email"], key_data=json.dumps(sa)
            )
    except Exception:
        pass

    # 2) 環境変数で鍵ファイルを指定
    key_path = os.environ.get("EE_SERVICE_ACCOUNT_JSON")
    if key_path and os.path.exists(key_path):
        with open(key_path) as f:
            sa = json.load(f)
        return ee.ServiceAccountCredentials(sa["client_email"], key_path)

    return None


def initialize() -> str:
    """Earth Engine を初期化し、使用した認証方式を返す。"""
    credentials = _try_service_account()
    if credentials is not None:
        ee.Initialize(credentials)
        return "service_account"

    project = os.environ.get(PROJECT_ENV, "").strip()
    if not project:
        raise ProjectNotSetError()

    try:
        ee.Initialize(project=project)
    except Exception:
        ee.Authenticate()
        ee.Initialize(project=project)
    return "personal"
