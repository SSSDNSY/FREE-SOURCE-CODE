# crawler.py
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

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

BASE_URL = "https://learn.lianglianglee.com/"
OUTPUT_DIR = "技术摘抄"
BOOK_LIST_FILE = "doc.txt"

def clean_filename(name):
    name = unquote(name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name.strip())
    return re.sub(r'\s+', '_', name)[:100] or "unnamed"

def load_books():
    if not os.path.exists(BOOK_LIST_FILE):
        raise FileNotFoundError(f"请创建 {BOOK_LIST_FILE}，每行一个专栏名")
    with open(BOOK_LIST_FILE, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

def download_global_static(session, output_dir):
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
        time.sleep(0.3)

def fix_internal_links(soup, current_book_dir, all_pages_map):
    # 1. 修复 /static/ 路径 → ../static/
    for tag in soup.find_all(['link', 'script'], href=True):
        if tag.get('href', '').startswith('/static/'):
            tag['href'] = '../static/' + tag['href'].split('/static/')[-1]
    for tag in soup.find_all('script', src=True):
        if tag.get('src', '').startswith('/static/'):
            tag['src'] = '../static/' + tag['src'].split('/static/')[-1]
    for tag in soup.find_all('link', href=True):
        if tag.get('href', '').startswith('/static/'):
            tag['href'] = '../static/' + tag['href'].split('/static/')[-1]

    # 2. 修复 favicon
    for img in soup.select('img[src]'):
        if img['src'].startswith('assets/'):
            # 保留 assets/，但确保已下载（由主逻辑处理）
            pass

    # 3. 修复侧边栏和“下一页”中的 .md 链接 → .html
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.endswith('.md'):
            # 构造目标本地路径
            page_name = href.split('/')[-1].replace('.md', '.html')
            if page_name in all_pages_map:
                a['href'] = all_pages_map[page_name]
            else:
                # 同目录下
                a['href'] = page_name

    # 4. 修复首页、上一级链接
    for a in soup.find_all('a', href=True):
        if a['href'] == '/':
            a['href'] = '../../index.html'
        elif a['href'] == '../':
            a['href'] = '../index.html'

    return soup

def main():
    books = load_books()
    print(f"📚 从 {BOOK_LIST_FILE} 加载 {len(books)} 个专栏")

    session = requests.Session()
    retry_strategy = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    adapter = CustomHTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})

    # 下载全局静态资源（一次）
    download_global_static(session, OUTPUT_DIR)

    for book in books:
        book_clean = clean_filename(book)
        book_dir = os.path.join(OUTPUT_DIR, book_clean)
        asset_dir = os.path.join(book_dir, "assets")
        os.makedirs(book_dir, exist_ok=True)
        print(f"\n📥 处理: {book_clean}")

        safe_book = quote(book, safe='')
        book_url = f"{BASE_URL}%E4%B8%93%E6%A0%8F/{safe_book}/"

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
            filename = href.split('/')[-1]
            md_links.append((full_url, filename))

        print(f"  📄 找到 {len(md_links)} 个页面")

        # 构建本专栏所有页面的映射：filename.html → filename.html（同目录）
        all_pages_map = {}
        for _, md_name in md_links:
            html_name = md_name.replace('.md', '.html')
            all_pages_map[html_name] = html_name

        for full_url, md_filename in md_links:
            html_filename = md_filename.replace('.md', '.html')
            local_path = os.path.join(book_dir, clean_filename(html_filename))
            if os.path.exists(local_path):
                print(f"  ✅ 已存在: {html_filename}")
                time.sleep(0.5)
                continue

            try:
                page_resp = session.get(full_url, timeout=15)
                page_resp.raise_for_status()
                page_soup = BeautifulSoup(page_resp.text, 'html.parser')

                # 下载图片
                for img in page_soup.find_all('img', src=True):
                    img_src = img['src']
                    if img_src.startswith('assets/'):
                        abs_img_url = urljoin(book_url, img_src)
                        try:
                            img_resp = session.get(abs_img_url, timeout=15)
                            if img_resp.status_code == 200:
                                os.makedirs(asset_dir, exist_ok=True)
                                img_name = unquote(img_src.split('/')[-1])
                                img_local = os.path.join(asset_dir, clean_filename(img_name))
                                with open(img_local, 'wb') as f:
                                    f.write(img_resp.content)
                        except Exception as e:
                            print(f"    ⚠️ 图片下载失败: {e}")

                # 修复链接
                fixed_soup = fix_internal_links(page_soup, book_dir, all_pages_map)

                with open(local_path, 'w', encoding='utf-8') as f:
                    f.write(str(fixed_soup))
                print(f"  ✅ 已保存: {html_filename}")
            except Exception as e:
                print(f"  ❌ 页面失败 {html_filename}: {e}")

            time.sleep(1.5)

        time.sleep(1.5)

    print(f"\n🎉 完成！离线内容保存至 ./{OUTPUT_DIR}/")

if __name__ == "__main__":
    main()