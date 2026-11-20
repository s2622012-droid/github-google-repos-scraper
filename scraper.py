import re
import sqlite3
import time
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://github.com/orgs/google/repositories"
DB_NAME = "google_repos.db"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def init_db(db_name: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_name)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS repositories")
    cur.execute(
        """
        CREATE TABLE repositories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_name TEXT NOT NULL,
            language TEXT,
            stars INTEGER
        )
        """
    )
    conn.commit()
    return conn


def parse_stars(star_text: str) -> int:
    text = star_text.strip().lower().replace(",", "")
    if not text:
        return 0
    if text.endswith("k"):
        return int(float(text[:-1]) * 1000)
    if text.endswith("m"):
        return int(float(text[:-1]) * 1_000_000)
    try:
        return int(text)
    except ValueError:
        return 0


def fetch_with_retry(url: str, max_retries: int = 3) -> requests.Response | None:
    """タイムアウトや一時的な接続エラー時に自動リトライする"""
    for attempt in range(1, max_retries + 1):
        try:
            # 課題要件: 必ず1秒待機
            time.sleep(1)
            response = requests.get(url, headers=HEADERS, timeout=30)
            if response.status_code == 429:
                wait_sec = attempt * 5
                print(f"  Rate limited (429). Waiting {wait_sec}s before retry...")
                time.sleep(wait_sec)
                continue
            return response
        except requests.exceptions.RequestException as e:
            print(f"  Connection error on attempt {attempt}/{max_retries}: {e}")
            if attempt < max_retries:
                time.sleep(3)
    return None


def scrape_google_repositories() -> list[tuple[str, str, int]]:
    results = []
    seen_repos = set()
    page = 1

    while True:
        url = f"{BASE_URL}?page={page}"
        print(f"Fetching page {page}: {url}")

        response = fetch_with_retry(url)
        if response is None or response.status_code != 200:
            code = response.status_code if response else "No Response"
            print(f"Failed to fetch page {page} (status: {code}). Stopping.")
            break

        soup = BeautifulSoup(response.text, "html.parser")

        repo_links = soup.find_all("a", href=re.compile(r"^/google/[^/]+$"))
        page_added = 0

        for link in repo_links:
            href = link.get("href", "")
            repo_name = href.split("/")[-1].strip()

            if not repo_name or repo_name in seen_repos:
                continue

            container = link.find_parent("li")
            if not container:
                container = link.find_parent("div", recursive=True)
                while container and not container.find("a", href=re.compile(r"/stargazers$")):
                    parent = container.find_parent("div")
                    if not parent or parent.name == "body":
                        break
                    container = parent

            if not container:
                continue

            star_tag = container.find("a", href=re.compile(rf"^/google/{re.escape(repo_name)}/stargazers$"))
            if not star_tag:
                continue

            stars = parse_stars(star_tag.get_text())

            language = "None"
            lang_tag = container.find("span", attrs={"itemprop": "programmingLanguage"})
            if lang_tag:
                language = lang_tag.get_text(strip=True)
            else:
                for span in container.find_all("span"):
                    txt = span.get_text(strip=True)
                    if txt and txt != repo_name and not re.match(r"^[\d.,km]+$", txt.lower()) and "Updated" not in txt and len(txt) < 25:
                        prev = span.find_previous_sibling("span")
                        if prev and ("background-color" in prev.get("style", "") or prev.get("class")):
                            language = txt
                            break

            seen_repos.add(repo_name)
            results.append((repo_name, language, stars))
            page_added += 1

        print(f"  -> {page_added} 件取得 (累計: {len(results)} 件)")

        if page_added == 0:
            print("No more repositories found. Finished scraping.")
            break

        page += 1

    return results


def main():
    conn = init_db(DB_NAME)
    cur = conn.cursor()

    repos = scrape_google_repositories()
    print(f"\n合計 {len(repos)} 件のリポジトリを取得しました。DBに保存します...")

    cur.executemany(
        "INSERT INTO repositories (repo_name, language, stars) VALUES (?, ?, ?)",
        repos,
    )
    conn.commit()

    print("\n=== DBに保存されたデータ（SELECT文による表示: 先頭30件） ===")
    cur.execute("SELECT id, repo_name, language, stars FROM repositories LIMIT 30")
    rows = cur.fetchall()
    for row in rows:
        print(f"ID: {row[0]:4d} | Name: {row[1]:35s} | Lang: {row[2]:15s} | Stars: {row[3]}")

    cur.execute("SELECT COUNT(*) FROM repositories")
    total_count = cur.fetchone()[0]
    print(f"\nDB内の総レコード数: {total_count} 件")

    conn.close()


if __name__ == "__main__":
    main()
