"""Earth Engine 認証モジュール。

認証の優先順位:
1. サービスアカウント (一般公開デプロイ用)
   - Streamlit secrets の [ee_service_account] セクション、または
   - 環境変数 EE_SERVICE_ACCOUNT_JSON (鍵ファイルへのパス)
2. 個人アカウント (ローカル/チーム利用)
   - `earthengine authenticate` 済みの資格情報を使用

将来 Google との交渉でアプリを一般公開できることになったら、
サービスアカウントの鍵を .streamlit/secrets.toml に置くだけで
コード変更なしで切り替わる設計。
"""

import json
import os

import ee

# 個人認証時に使う GCP プロジェクトID (環境変数で上書き可)
DEFAULT_PROJECT = os.environ.get("EE_PROJECT", "ee-naoki")


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

    try:
        ee.Initialize(project=DEFAULT_PROJECT)
    except Exception:
        ee.Authenticate()
        ee.Initialize(project=DEFAULT_PROJECT)
    return "personal"
