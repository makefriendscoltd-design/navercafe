"""Shared anonymous YouTube extraction settings for production sources."""


def source_options() -> dict:
    # The plain anonymous clients get "Sign in to confirm you're not a bot"
    # once this IP has been rate limited, which blocks both downloads and the
    # duration measurement the acceptance gate needs. web_embedded still serves
    # full progressive and adaptive MP4 formats under that block, so it leads
    # and yt-dlp's own client selection stays as the fallback.
    #
    # Forcing mweb without a GVS PO token removes adaptive formats and can
    # leave no downloadable video, so it is never pinned here.
    #
    # Sources are fetched one at a time; the sleeps keep a burst of requests
    # from earning the rate limit in the first place.
    return {"quiet": True, "no_warnings": False, "noplaylist": True,
            "js_runtimes": {"node": {}},
            "extractor_args": {"youtube": {"player_client": ["web_embedded", "default"]}},
            "sleep_interval_requests": 1, "sleep_interval": 2, "max_sleep_interval": 6,
            "retries": 5, "extractor_retries": 3}
