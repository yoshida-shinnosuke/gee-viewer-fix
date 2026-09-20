"""Earth Engine 認証モジュール。

認証の優先順位:
1. サービスアカウント (一般公開デプロイ用)
   - Streamlit secrets の [ee_service_account] セクション、または
   - 環境変数 EE_SERVICE_ACCOUNT_JSON (鍵ファイルへのパス)
2. 個人アカウント (ローカル/チーム利用)
   - `earthengine authenticate` 済みの資格情報を使用
   - GCP プロジェクトは環境変数 EE_PROJECT で指定する(既定値は無い)。
     プロジェクト直下の .env があれば読み込む

将来 Google との交渉でアプリを一般公開できることになったら、
サービスアカウントの鍵を .streamlit/secrets.toml に置くだけで
コード変更なしで切り替わる設計。
"""

import json
import os
from pathlib import Path

import ee

PROJECT_ENV = "EE_PROJECT"
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """プロジェクト直下の .env から KEY=VALUE を読み、環境変数に補う。

    追加パッケージ(python-dotenv)を使わない最小の実装。読むのは次の書式だけ。
    - `KEY=VALUE` の行(値は前後の空白と、対になった引用符を取り除く)
    - 空行と `#` で始まる行は無視する(行末コメントは非対応)

    すでに設定済みの環境変数は上書きしない。シェルや CI の値が .env より優先される。
    """
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


class ProjectNotSetError(RuntimeError):
    """個人認証で使う GCP プロジェクトID(環境変数 EE_PROJECT)が未設定。

    メッセージは画面にそのまま出せる形(解決方法つき)で組み立てる。
    """

    def __init__(self) -> None:
        super().__init__(
            f"環境変数 `{PROJECT_ENV}` が設定されていません。"
            "Earth Engine を使う自分の GCP プロジェクトIDを指定してください。\n\n"
            "1. `.env.sample` を `.env` にコピーする(プロジェクト直下)\n"
            "2. `.env` の `EE_PROJECT=` の値を自分のIDに書き換える\n"
            "3. この画面を再読み込みする\n\n"
            "`.env` の代わりに、シェルで `export EE_PROJECT=...` してもよい"
            "(設定済みの環境変数が `.env` より優先される)。\n\n"
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

    load_env_file()
    project = os.environ.get(PROJECT_ENV, "").strip()
    if not project:
        raise ProjectNotSetError()

    try:
        ee.Initialize(project=project)
    except Exception:
        ee.Authenticate()
        ee.Initialize(project=project)
    return "personal"
