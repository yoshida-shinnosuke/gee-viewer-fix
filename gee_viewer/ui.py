"""ParamSpec から Streamlit ウィジェットを生成する。

各段が自分の必要なパラメータを ParamSpec で宣言するので、app.py に
「この解析のときだけこのスライダーを出す」という分岐を書かずに済む。
新しい解析を追加しても app.py は変更不要。
"""

from __future__ import annotations

from collections.abc import Iterable

import streamlit as st

from .pipeline import ParamSpec, Source


def render_params(
    specs: Iterable[ParamSpec],
    key_prefix: str,
    source: Source | None = None,
    container=None,
) -> dict:
    """ParamSpec 群をウィジェットとして描画し、{名前: 値} を返す。"""
    c = container or st
    values: dict = {}
    for spec in specs:
        key = f"{key_prefix}:{spec.name}"
        if spec.kind == "int":
            values[spec.name] = c.slider(
                spec.label, int(spec.min), int(spec.max), int(spec.default),
                int(spec.step or 1), key=key, help=spec.help or None,
            )
        elif spec.kind == "float":
            values[spec.name] = c.slider(
                spec.label, float(spec.min), float(spec.max), float(spec.default),
                float(spec.step or 0.01), key=key, help=spec.help or None,
            )
        elif spec.kind == "bool":
            values[spec.name] = c.checkbox(
                spec.label, bool(spec.default), key=key, help=spec.help or None
            )
        elif spec.kind == "select":
            options = list(spec.options)
            if spec.options_from_source is not None and source is not None:
                options = list(spec.options_from_source(source))
            if not options:
                continue
            default = spec.default if spec.default in options else options[0]
            values[spec.name] = c.selectbox(
                spec.label, options, index=options.index(default),
                key=key, help=spec.help or None,
            )
    return values
