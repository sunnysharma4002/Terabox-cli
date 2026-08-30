import json
import re
import urllib.parse
from http import HTTPStatus
import requests
import urllib3
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class TeraboxDownloader:
    def __init__(self):
        self.session = requests.Session()
        self.session.verify = False
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Mobile Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'en-MM,en-GB;q=0.9,en-US;q=0.8,en;q=0.7',
            'Sec-Ch-Ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
            'Sec-Ch-Ua-Mobile': '?1',
            'Sec-Ch-Ua-Platform': '"Android"'
        }
        self.base_url = "https://terabox.beer"

    def extract_video_id(self, url):
        patterns = [
            r'/s/([a-zA-Z0-9_-]+)',
            r'share\.com/s/([a-zA-Z0-9_-]+)',
            r'file\.com/s/([a-zA-Z0-9_-]+)'
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def extract_m3u8_url(self, text):
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

    def follow_redirects(self, url, max_redirects=5):
        current_url = url
        redirect_count = 0
        while redirect_count < max_redirects:
            try:
                response = self.session.get(
                    current_url,
                    headers=self.headers | {'Referer': self.base_url + '/'},
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
                    m3u8_url = self.extract_m3u8_url(response.text)
                    if m3u8_url:
                        return {'final_url': current_url, 'm3u8_url': m3u8_url, 'response': response}
                return {'final_url': current_url, 'm3u8_url': None, 'response': response}
            except Exception:
                return {'final_url': current_url, 'm3u8_url': None, 'response': None}
        return {'final_url': current_url, 'm3u8_url': None, 'response': None}

    def process_terabox_link(self, terabox_url):
        video_id = self.extract_video_id(terabox_url)
        if not video_id:
            return {"error": "Could not extract video ID from the link"}

        self.session.get(
            self.base_url,
            headers=self.headers | {'Referer': 'https://www.google.com/'}
        )

        watch_url = f"{self.base_url}/watch/{video_id}"
        self.session.get(
            watch_url,
            headers=self.headers | {'Referer': self.base_url + '/'}
        )

        encoded_url = urllib.parse.quote(terabox_url, safe='')
        api_url = f"{self.base_url}/api/terabox-new?link={encoded_url}"
        response = self.session.get(
            api_url,
            headers=self.headers | {'Referer': watch_url}
        )

        try:
            api_result = response.json()
        except Exception:
            return {"error": "Failed to parse API response"}

        if isinstance(api_result, dict):
            if api_result.get('error') == False:
                video_url = None
                possible_fields = [
                    'stream_download_url', 'download_link', 'fallback_url',
                    'proxy_url', 'url', 'video_url'
                ]
                for field in possible_fields:
                    if field in api_result and api_result[field]:
                        video_url = api_result[field]
                        break
                if not video_url:
                    for key, value in api_result.items():
                        if isinstance(value, str) and (value.startswith('http://') or value.startswith('https://')):
                            video_url = value
                            break
                if not video_url:
                    return {"error": "No video URL found in API response"}

                file_name = api_result.get('file_name', 'Unknown')
                file_size = api_result.get('file_size', 'Unknown')
            else:
                error_msg = api_result.get('error') or api_result.get('message') or 'Unknown error'
                return {"error": f"API request failed: {error_msg}"}
        else:
            return {"error": "API response is not JSON"}

        redirect_result = self.follow_redirects(video_url)
        if redirect_result['m3u8_url']:
            final_video_url = redirect_result['m3u8_url']
        else:
            final_video_url = video_url

        return {
            "success": True,
            "video_id": video_id,
            "video_url": final_video_url,
            "original_url": video_url,
            "watch_page": watch_url,
            "file_name": file_name,
            "file_size": file_size
        }


def handler(request):
    if request.method == 'OPTIONS':
        return {'statusCode': 200, 'headers': {'Access-Control-Allow-Origin': '*'}}

    if request.method != 'GET':
        return {
            'statusCode': HTTPStatus.METHOD_NOT_ALLOWED,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Method not allowed. Use GET.'})
        }

    params = request.args if hasattr(request, 'args') else {}
    url = params.get('url') or request.query_string

    if not url:
        return {
            'statusCode': HTTPStatus.BAD_REQUEST,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Missing "url" query parameter. Example: /?url=https://terabox.com/s/abc123'})
        }

    downloader = TeraboxDownloader()
    result = downloader.process_terabox_link(url)

    status = HTTPStatus.OK if 'error' not in result else HTTPStatus.BAD_REQUEST
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
        'body': json.dumps(result, indent=2)
    }
