"""場所名からの検索(ジオコーディング)。

OpenStreetMap の Nominatim API を使い、「鳥取砂丘」のような場所名を
緯度経度に変換する。認証不要・無償だが、利用ポリシー上

    - リクエストには User-Agent(または Referer)を付ける
    - 高頻度アクセスをしない(目安: 1 req/sec 以下)

ことが求められる(https://operations.osmfoundation.org/policies/nominatim/)。
このアプリではユーザー操作(検索ボタン)1回につき1リクエストしか
発生しないため、通常利用では問題にならない。
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
TIMEOUT = 10
# Nominatim は User-Agent 未設定のリクエストを拒否しうるため、
# 連絡先の分かるアプリ名を名乗る(Nominatimの利用ポリシー上の要件)。
USER_AGENT = "gee-viewer (https://github.com/org-oz/gee-viewer)"


@dataclass(frozen=True)
class Place:
    """検索結果1件。"""

    name: str
    lat: float
    lon: float


@lru_cache(maxsize=128)
def search(query: str, limit: int = 5) -> tuple[Place, ...]:
    """場所名で検索し、確からしい順の候補を返す。0件なら空タプル。

    同じクエリを何度も引かないよう lru_cache でキャッシュする
    (Nominatim の利用ポリシーが求める低頻度アクセスにも資する)。
    """
    query = query.strip()
    if not query:
        return ()
    params = urllib.parse.urlencode(
        {"q": query, "format": "jsonv2", "limit": limit, "accept-language": "ja"}
    )
    req = urllib.request.Request(
        f"{NOMINATIM_URL}?{params}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        results = json.load(r)
    return tuple(
        Place(name=item["display_name"], lat=float(item["lat"]), lon=float(item["lon"]))
        for item in results
    )
