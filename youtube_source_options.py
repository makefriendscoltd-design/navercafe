"""Shared anonymous YouTube extraction settings for production sources."""


def source_options() -> dict:
    # Let yt-dlp select its supported clients. Forcing mweb without a GVS PO
    # token removes adaptive formats and can leave no downloadable video.
    return {"quiet": True, "no_warnings": False, "noplaylist": True,
            "js_runtimes": {"node": {}}}
