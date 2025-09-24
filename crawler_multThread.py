# -*- coding: utf-8 -*-
import os
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager
from urllib3.util.ssl_ import create_urllib3_context
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from urllib.parse import urljoin, unquote, quote
import re
import urllib3
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================== 配置 ==================
BASE_URL = "https://learn.lianglianglee.com/"
OUTPUT_DIR = "技术摘抄"
BOOK_LIST_FILE = "doc.txt"
MAX_WORKERS = 1  # 改为单线程

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
]


# =========================================

class CustomHTTPAdapter(HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        ctx = create_urllib3_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=ctx,
            assert_hostname=False,
            cert_reqs='CERT_NONE',
            **pool_kwargs
        )

def clean_filename(name):
    name = unquote(name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name.strip())
    return re.sub(r'\s+', '_', name)[:100] or "unnamed"

def load_books():
    if not os.path.exists(BOOK_LIST_FILE):
        raise FileNotFoundError(f"❌ 请创建 {BOOK_LIST_FILE} 文件（UTF-8 编码），每行一个专栏名")
    with open(BOOK_LIST_FILE, 'r', encoding='utf-8') as f:
        books = [line.strip() for line in f if line.strip()]
    return books

def download_global_static(output_dir):
    """下载全局静态资源到 static/ 目录"""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=10,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = CustomHTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.headers.update({'User-Agent': random.choice(USER_AGENTS)})

    static_dir = os.path.join(output_dir, "static")
    os.makedirs(static_dir, exist_ok=True)
    static_files = [
        "index.css", "highlight.min.css", "highlight.min.js",
        "index.js", "main.js", "email-decode.min.js", "favicon.png"
    ]
    for fname in static_files:
        url = urljoin(BASE_URL, f"/static/{fname}")
        local_path = os.path.join(static_dir, fname)
        if os.path.exists(local_path):
            continue
        try:
            resp = session.get(url, timeout=15)
            if resp.status_code == 200:
                with open(local_path, 'wb') as f:
                    f.write(resp.content)
                print(f"  📦 下载静态资源: {fname}")
        except Exception as e:
            print(f"  ⚠️ 静态资源失败 {fname}: {e}")
        time.sleep(0.1)

def fix_links_in_html(html_content, md_to_html_map, book_dir):
    """修复 HTML 中的链接：.md → .html，/static/ → ../static/"""
    soup = BeautifulSoup(html_content, 'html.parser')

    # 1. 修复 /static/ 路径
    for tag in soup.find_all(['link', 'script']):
        if tag.get('href', '').startswith('/static/'):
            tag['href'] = '../static/' + tag['href'].split('/static/')[-1]
        if tag.get('src', '').startswith('/static/'):
            tag['src'] = '../static/' + tag['src'].split('/static/')[-1]

    # 2. 修复 favicon（首页链接）
    for img in soup.select('img[src="/static/favicon.png"]'):
        img['src'] = '../static/favicon.png'

    # 3. 修复内部 .md 链接 → .html
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.endswith('.md'):
            # 尝试匹配映射
            filename_md = href.split('/')[-1]
            target_html = filename_md.replace('.md', '.html')
            if target_html in md_to_html_map.values():
                a['href'] = target_html
        elif href == '/':
            a['href'] = '../../index.html'
        elif href == '../':
            a['href'] = '../index.html'

    return str(soup)

def download_single_page(args):
    full_url, md_filename, html_filename, book_dir, asset_dir, md_to_html_map = args
    local_path = os.path.join(book_dir, html_filename)
    if os.path.exists(local_path):
        return f"✅ 已存在: {html_filename}"

    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=10,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = CustomHTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.headers.update({'User-Agent': random.choice(USER_AGENTS)})

    # 随机延迟
    delay = random.uniform(8, 15)
    time.sleep(delay)
    try:
        resp = session.get(full_url, timeout=15)
        resp.raise_for_status()
        content = resp.text

        # 下载图片
        page_soup = BeautifulSoup(content, 'html.parser')
        for img in page_soup.find_all('img', src=True):
            img_src = img['src']
            if img_src.startswith('assets/'):
                abs_img_url = urljoin(full_url, img_src)
                try:
                    img_resp = session.get(abs_img_url, timeout=15)
                    if img_resp.status_code == 200:
                        os.makedirs(asset_dir, exist_ok=True)
                        img_name = unquote(img_src.split('/')[-1])
                        img_local = os.path.join(asset_dir, clean_filename(img_name))
                        with open(img_local, 'wb') as f:
                            f.write(img_resp.content)
                except:
                    pass

        # 修复链接
        fixed_html = fix_links_in_html(content, md_to_html_map, book_dir)

        with open(local_path, 'w', encoding='utf-8') as f:
            f.write(fixed_html)
        return f"✅ 已保存: {html_filename}"
    except Exception as e:
        return f"❌ 失败 {html_filename}: {str(e)[:60]}"

def main():
    books = load_books()
    print(f"📚 从 {BOOK_LIST_FILE} 加载 {len(books)} 个专栏")

    # 下载全局静态资源
    print("📥 下载全局静态资源...")
    download_global_static(OUTPUT_DIR)

    for book in books:
        book_clean = clean_filename(book)
        book_dir = os.path.join(OUTPUT_DIR, book_clean)
        asset_dir = os.path.join(book_dir, "assets")
        os.makedirs(book_dir, exist_ok=True)
        print(f"\n📥 处理专栏: {book_clean}")

        safe_book = quote(book, safe='')
        book_url = f"{BASE_URL}%E4%B8%93%E6%A0%8F/{safe_book}/"

        session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=10,
            status_forcelist=[429, 500, 502, 503, 504],
        )
        adapter = CustomHTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.headers.update({'User-Agent': random.choice(USER_AGENTS)})

        try:
            resp = session.get(book_url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            print(f"  ⚠️ 专栏页面失败: {e}")
            time.sleep(3)
            continue

        soup = BeautifulSoup(resp.text, 'html.parser')
        md_links = []
        for a in soup.select('a[href$=".md"]'):
            href = a['href']
            full_url = urljoin(book_url, href)
            md_filename = href.split('/')[-1]
            html_filename = clean_filename(md_filename.replace('.md', '.html'))
            md_links.append((full_url, md_filename, html_filename))

        if not md_links:
            print("  ⚠️ 未找到任何 .md 页面")
            continue

        # 构建映射：用于链接修复
        md_to_html_map = {md: html for _, md, html in md_links}

        print(f"  📄 找到 {len(md_links)} 个页面，开始并发下载（{MAX_WORKERS}线程）...")

        # 准备参数
        tasks = [
            (full_url, md_filename, html_filename, book_dir, asset_dir, md_to_html_map)
            for full_url, md_filename, html_filename in md_links
        ]

        # 并发下载
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(download_single_page, task) for task in tasks]
            for future in as_completed(futures):
                print(f"  {future.result()}")
                time.sleep(5)  # 防止单线程过快

        time.sleep(2)

    print(f"\n🎉 全部完成！离线内容保存至 ./{OUTPUT_DIR}/")

if __name__ == "__main__":
    main()