FETCH_URL_TOOL_NAME = 'FetchUrl'

DESCRIPTION = (
    'Fetch a URL and extract clean page text using trafilatura (strips '
    'navigation/ads/sidebar). Returns markdown or plain text. Long pages '
    '(> char_limit, default 15000) are head+tail truncated and the full '
    'text is saved to cache/web/ with a stored_path; use Read tool with '
    'offset/limit to read the omitted middle. SSRF-protected: refuses '
    'loopback/private/SSRF-unsafe addresses. Pair with WebSearch: search '
    'to find URLs, then FetchUrl to read full content.'
)
