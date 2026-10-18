from gee_viewer.ui import build_basemap_layers


def test_basemap_layers_use_single_selected_source_and_hide_duplicate_control():
    layers = build_basemap_layers(
        (
            ("OpenStreetMap", "OpenStreetMap", "OpenStreetMap"),
            ("背景なし", "", " "),
        ),
        "背景なし",
    )

    assert len(layers) == 2
    assert sum(layer["show"] for layer in layers) == 1
    assert all(layer["control"] is False for layer in layers)
    assert [layer["name"] for layer in layers if layer["show"]] == ["背景なし"]
