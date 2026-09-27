"""画像の有無の問い合わせ(いつ・何枚あるか)。

STAC から分かるのはデータセット全体の提供期間だけで、「この場所に
いつの画像があるか」は EE に聞かないと分からない。ここでは
pipeline.filtered_collection(= 実際に合成される画像の集合)に対して
枚数と撮影日時だけを問い合わせる。ピクセルは一切取得しない。

毎時データ(ERA5 など)は1地点でも数十万枚あり、素直に数えると
数分かかる。limit(CAP + 1) で上限付きで数え、上限を超えるものは
「常時データがある」とみなして月別集計を省略する。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import ee

from .pipeline import Source, filtered_collection

# これを超える枚数は数えない(超えたかどうかだけ分かれば十分)
CAP = 5000


def count(src: Source, region, start: str, end: str, params: dict) -> int:
    """期間・領域・フィルタに合う画像の枚数。CAP を超える場合は CAP + 1。"""
    col = filtered_collection(src, region, start, end, params)
    return col.limit(CAP + 1).size().getInfo()


def scene_dates(src: Source, region, params: dict) -> list[str] | None:
    """提供期間全体での撮影日("YYYY-MM-DD"、UTC、1枚につき1要素、昇順)。

    期間では絞らない(フィルタは適用する)。CAP を超える場合は None。
    日付への変換はサーバー側で image.date() を使うより、撮影時刻の配列を
    受け取って手元で行うほうが速い(実測で同等〜半分程度の時間)。
    """
    col = filtered_collection(src, region, None, None, params)
    stamps = col.limit(CAP + 1).aggregate_array("system:time_start").getInfo()
    if len(stamps) > CAP:
        return None
    return sorted(
        datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        for t in stamps
        if t is not None
    )


def monthly_counts(dates: list[str]) -> dict[str, int]:
    """撮影日のリストを {"YYYY-MM": 枚数} に集計する(月順)。"""
    out: dict[str, int] = {}
    for d in sorted(dates):
        out[d[:7]] = out.get(d[:7], 0) + 1
    return out


def nearest_dates(
    dates: list[str], start: date, end: date
) -> tuple[str | None, str | None]:
    """指定期間の直前・直後で画像のある日を返す(無ければ None)。

    filterDate(start, end) は end を含まないので、end 当日は「直後」側に入る。
    """
    before = [d for d in dates if d < start.isoformat()]
    after = [d for d in dates if d >= end.isoformat()]
    return (before[-1] if before else None, after[0] if after else None)
