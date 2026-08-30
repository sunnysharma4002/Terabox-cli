import json
import re
import urllib.parse
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://terabox.beer"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'en-MM,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
    'Sec-Ch-Ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
    'Sec-Ch-Ua-Mobile': '?1',
    'Sec-Ch-Ua-Platform': '"Android"'
}


def extract_video_id(url):
    patterns = [r'/s/([a-zA-Z0-9_-]+)', r'share\.com/s/([a-zA-Z0-9_-]+)', r'file\.com/s/([a-zA-Z0-9_-]+)']
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def extract_m3u8_url(text):
    patterns = [
        r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+/playlist\.m3u8[^\s"\'<>]*)',
        r'(https?://[^\s"\'<>]+\.m3u8\?[^\s"\'<>]*)'
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    return None


def follow_redirects(url, max_redirects=5):
    session = requests.Session()
    session.verify = False
    current_url = url
    redirect_count = 0
    while redirect_count < max_redirects:
        try:
            response = session.get(
                current_url,
                headers={**HEADERS, 'Referer': BASE_URL + '/'},
                allow_redirects=False,
                timeout=30
            )
            if response.status_code in [301, 302, 303, 307, 308]:
                location = response.headers.get('Location')
                if location:
                    if location.startswith('/'):
                        parsed = urllib.parse.urlparse(current_url)
                        location = f"{parsed.scheme}://{parsed.netloc}{location}"
                    elif not location.startswith('http'):
                        parsed = urllib.parse.urlparse(current_url)
                        base = f"{parsed.scheme}://{parsed.netloc}"
                        if not location.startswith('/'):
                            base += '/' + '/'.join(parsed.path.split('/')[:-1])
                        location = base + '/' + location.lstrip('/')
                    current_url = location
                    redirect_count += 1
                    continue
            if response.text:
                m3u8_url = extract_m3u8_url(response.text)
                if m3u8_url:
                    return {'final_url': current_url, 'm3u8_url': m3u8_url}
            return {'final_url': current_url, 'm3u8_url': None}
        except Exception:
            return {'final_url': current_url, 'm3u8_url': None}
    return {'final_url': current_url, 'm3u8_url': None}


def process_terabox_link(terabox_url):
    video_id = extract_video_id(terabox_url)
    if not video_id:
        return {"error": "Could not extract video ID from the link"}

    session = requests.Session()
    session.verify = False

    session.get(BASE_URL, headers={**HEADERS, 'Referer': 'https://www.google.com/'})
    watch_url = f"{BASE_URL}/watch/{video_id}"
    session.get(watch_url, headers={**HEADERS, 'Referer': BASE_URL + '/'})

    encoded_url = urllib.parse.quote(terabox_url, safe='')
    api_url = f"{BASE_URL}/api/terabox-new?link={encoded_url}"
    response = session.get(api_url, headers={**HEADERS, 'Referer': watch_url})

    try:
        api_result = response.json()
    except Exception:
        return {"error": "Failed to parse API response"}

    if not isinstance(api_result, dict):
        return {"error": "API response is not JSON"}

    if api_result.get('error') != False:
        error_msg = api_result.get('error') or api_result.get('message') or 'Unknown error'
        return {"error": f"API request failed: {error_msg}"}

    video_url = None
    for field in ['stream_download_url', 'download_link', 'fallback_url', 'proxy_url', 'url', 'video_url']:
        if field in api_result and api_result[field]:
            video_url = api_result[field]
            break
    if not video_url:
        for key, value in api_result.items():
            if isinstance(value, str) and value.startswith('http'):
                video_url = value
                break
    if not video_url:
        return {"error": "No video URL found in API response"}

    file_name = api_result.get('file_name', 'Unknown')
    file_size = api_result.get('file_size', 'Unknown')

    redirect_result = follow_redirects(video_url)
    final_video_url = redirect_result['m3u8_url'] if redirect_result['m3u8_url'] else video_url

    return {
        "success": True,
        "video_id": video_id,
        "video_url": final_video_url,
        "original_url": video_url,
        "watch_page": watch_url,
        "file_name": file_name,
        "file_size": file_size
    }


def parse_query_string(query_string):
    if not query_string:
        return {}
    params = {}
    for pair in query_string.split('&'):
        if '=' in pair:
            key, value = pair.split('=', 1)
            params[urllib.parse.unquote_plus(key)] = urllib.parse.unquote_plus(value)
        else:
            params[urllib.parse.unquote_plus(pair)] = ''
    return params


def app(environ, start_response):
    method = environ.get('REQUEST_METHOD', 'GET')
    path = environ.get('PATH_INFO', '/')

    headers = [
        ('Content-Type', 'application/json'),
        ('Access-Control-Allow-Origin', '*'),
        ('Access-Control-Allow-Methods', 'GET, OPTIONS'),
        ('Access-Control-Allow-Headers', 'Content-Type')
    ]

    if method == 'OPTIONS':
        start_response('200 OK', headers)
        return [b'{}']

    if method != 'GET':
        start_response('405 Method Not Allowed', headers)
        return [json.dumps({'error': 'Method not allowed'}).encode()]

    query_string = environ.get('QUERY_STRING', '')
    params = parse_query_string(query_string)
    url = params.get('url', '')

    if not url:
        start_response('400 Bad Request', headers)
        return [json.dumps({'error': 'Missing "url" query parameter. Example: /?url=https://terabox.com/s/abc123'}).encode()]

    result = process_terabox_link(url)
    status = '200 OK' if 'error' not in result else '400 Bad Request'
    start_response(status, headers)
    return [json.dumps(result, indent=2).encode()]
