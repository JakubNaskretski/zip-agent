"""check_asset_fit — pure math comparing an asset's aspect to a slot rect.

Asset metadata: {"width": px, "height": px, "aspect": w/h}.
Slot rect: fractional {x, y, w, h} on the canvas.

Returns whether the asset fits cleanly, what crop is needed at fit='fill',
and how off the aspect is.
"""


def check_asset_fit(
    asset: dict,
    cell_rect: dict,
    *,
    canvas_w_in: float = 13.333,
    canvas_h_in: float = 7.5,
    fit_mode: str = "fill",
) -> dict:
    asset_w = asset.get("width")
    asset_h = asset.get("height")
    asset_aspect = asset.get("aspect")
    if asset_aspect is None:
        if asset_w and asset_h:
            asset_aspect = asset_w / asset_h
        else:
            return {
                "fits": False,
                "reason": "asset is missing width/height/aspect",
                "suggestion": "Re-run describe_assets.py to fill mechanical dims.",
            }

    slot_w_in = cell_rect["w"] * canvas_w_in
    slot_h_in = cell_rect["h"] * canvas_h_in
    slot_aspect = slot_w_in / slot_h_in
    aspect_delta = asset_aspect - slot_aspect

    # 5% tolerance is "clean fit"
    tolerance = 0.05 * slot_aspect
    clean_fit = abs(aspect_delta) <= tolerance

    if fit_mode == "fill":
        if asset_aspect > slot_aspect:
            # Image wider than slot — crop sides
            scaled_h_in = slot_h_in
            scaled_w_in = scaled_h_in * asset_aspect
            crop_w_frac = (scaled_w_in - slot_w_in) / scaled_w_in
            will_crop = {"sides": "horizontal", "fraction": round(crop_w_frac, 3)}
        elif asset_aspect < slot_aspect:
            # Image taller than slot — crop top/bottom
            scaled_w_in = slot_w_in
            scaled_h_in = scaled_w_in / asset_aspect
            crop_h_frac = (scaled_h_in - slot_h_in) / scaled_h_in
            will_crop = {"sides": "vertical", "fraction": round(crop_h_frac, 3)}
        else:
            will_crop = None
    else:  # contain
        will_crop = None

    return {
        "fits": clean_fit,
        "clean_fit": clean_fit,
        "aspect_delta": round(aspect_delta, 3),
        "asset_aspect": round(asset_aspect, 3),
        "slot_aspect": round(slot_aspect, 3),
        "will_crop": will_crop,
        "suggestion": (
            None
            if clean_fit
            else (
                f"Aspect mismatch ({asset_aspect:.2f} vs slot {slot_aspect:.2f}). "
                f"Consider an asset closer to {slot_aspect:.2f} aspect "
                f"or accept fit='fill' crop."
            )
        ),
    }
