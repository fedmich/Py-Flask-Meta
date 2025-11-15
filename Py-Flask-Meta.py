import os
import hashlib
import json
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from flask import Flask, render_template, request, jsonify

# === Config ===
app = Flask(__name__)

CACHE_HTML_DIR = "./cache-html"
CACHE_JSON_DIR = "./meta-json"
os.makedirs(CACHE_HTML_DIR, exist_ok=True)
os.makedirs(CACHE_JSON_DIR, exist_ok=True)

# 60 days in seconds
HTML_CACHE_TTL = 60 * 60 * 24 * 60

# Rotating User-Agent list
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 12_10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.3 Mobile/15E148 Safari/604.1"
]


def get_md5(url):
    return hashlib.md5(url.encode('utf-8')).hexdigest()


def is_html_fresh(filepath):
    if not os.path.exists(filepath):
        return False
    file_time = datetime.fromtimestamp(os.path.getmtime(filepath))
    return datetime.now() - file_time < timedelta(seconds=HTML_CACHE_TTL)


def extract_og_metadata(html):
    soup = BeautifulSoup(html, 'html.parser')
    og_data = {}
    for tag in soup.find_all('meta', property=lambda x: x and x.startswith('og:')):
        prop = tag.get('property')
        content = tag.get('content')
        if prop and content:
            og_data[prop] = content
    if 'og:image' not in og_data:
        twitter_img = soup.find(
            'meta', attrs={'name': 'twitter:image', 'content': True})
        if twitter_img:
            og_data['og:image'] = twitter_img['content']
    return og_data


def fetch_with_requests(url, referer):
    headers = {
        'User-Agent': USER_AGENTS[hash(url) % len(USER_AGENTS)],
        'Referer': referer,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    try:
        response = requests.get(url, headers=headers,
                                timeout=10, allow_redirects=True)
        response.raise_for_status()
        return response.text, response.url
    except Exception as e:
        print(f"[requests] Failed: {e}")
        return None, None


def fetch_with_curl_cffi(url, referer):
    headers = {'Referer': referer}
    try:
        versions = ["chrome110", "chrome107", "edge101", "safari15_3"]
        impersonate = versions[hash(url) % len(versions)]
        response = cffi_requests.get(
            url, impersonate=impersonate, headers=headers, timeout=15, allow_redirects=True
        )
        if response.status_code == 200:
            return response.text, response.url
    except Exception as e:
        print(f"[curl_cffi] Failed: {e}")
    return None, None


@app.route('/test')
def test():
    return "Working!"


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/meta', methods=['GET', 'POST'])
def meta():
    url = request.args.get('url') or request.form.get('url')
    if not url:
        return jsonify({"error": "URL is required"}), 400

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return jsonify({"error": "Invalid URL"}), 400

    referer = f"{parsed.scheme}://{parsed.netloc}"
    cache_key = get_md5(url)
    html_cache_path = os.path.join(CACHE_HTML_DIR, f"{cache_key}.txt")
    json_cache_path = os.path.join(CACHE_JSON_DIR, f"{cache_key}.json")

    if os.path.exists(json_cache_path):
        try:
            with open(json_cache_path, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f))
        except Exception as e:
            print(f"⚠️ Read JSON cache failed: {e}")

    html_content = None
    final_url = url
    if os.path.exists(html_cache_path) and is_html_fresh(html_cache_path):
        with open(html_cache_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
    else:
        html_content, final_url = fetch_with_requests(url, referer)
        if not html_content:
            print("⚠️ Falling back to curl_cffi...")
            html_content, final_url = fetch_with_curl_cffi(url, referer)

        if html_content:
            with open(html_cache_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
        else:
            return jsonify({"error": "Failed to fetch page"}), 500

    og_data = extract_og_metadata(html_content)
    title_tag = BeautifulSoup(html_content, 'html.parser').find('title')
    title = title_tag.get_text().strip() if title_tag else None

    result = {
        "url": url,
        "final_url": final_url,
        "title": title,
        "og:title": og_data.get("og:title"),
        "og:description": og_data.get("og:description"),
        "og:image": og_data.get("og:image"),
        "og:url": og_data.get("og:url"),
        "og:site_name": og_data.get("og:site_name"),
        "timestamp": time.time()
    }

    try:
        with open(json_cache_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Write JSON cache failed: {e}")

    return jsonify(result)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
