#!/usr/bin/env python3
"""
清水河畔论坛 - 兼职信息发布栏监控脚本
通过搜索获取帖子，过滤出兼职信息发布栏(fid=183)中含"家教"的新帖，发送QQ邮件通知
"""

import os
import re
import time
import random
import smtplib
import json
from email.mime.text import MIMEText
from email.header import Header
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

FORUM_URL = "https://bbs.uestc.edu.cn"
FORUM_FID = "183"  # 兼职信息发布栏

FORUM_USERNAME = os.environ.get("FORUM_USERNAME", "")
FORUM_PASSWORD = os.environ.get("FORUM_PASSWORD", "")

EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "")
EMAIL_AUTH_CODE = os.environ.get("EMAIL_AUTH_CODE", "")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT", "")

KEYWORD = "家教"
CACHE_FILE = "seen_ids.json"
TZ = timezone(timedelta(hours=8))


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]


def get_headers(referer: str = None) -> dict:
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def random_delay(min_s: float = 1.0, max_s: float = 4.0):
    time.sleep(random.uniform(min_s, max_s))


def login(session: requests.Session) -> bool:
    """登录论坛"""
    print("[*] 正在登录...")

    try:
        resp = session.get(f"{FORUM_URL}/forum.php", headers=get_headers(), timeout=30)
        resp.encoding = "utf-8"
    except requests.RequestException as e:
        print(f"[-] 网络请求失败: {e}")
        return False

    if resp.status_code != 200:
        return False

    fh = re.search(r'formhash" value="([a-f0-9]+)"', resp.text)
    if not fh:
        print("[-] 无法获取 formhash")
        return False

    formhash = fh.group(1)
    random_delay(2, 5)

    try:
        resp = session.post(
            f"{FORUM_URL}/member.php?mod=logging&action=login&loginsubmit=yes",
            data={
                "formhash": formhash,
                "loginfield": "username",
                "username": FORUM_USERNAME,
                "password": FORUM_PASSWORD,
                "cookietime": "2592000",
                "referer": f"{FORUM_URL}/forum.php",
            },
            headers=get_headers(referer=f"{FORUM_URL}/member.php?mod=logging&action=login"),
            allow_redirects=True,
            timeout=30,
        )
        resp.encoding = "utf-8"
    except requests.RequestException as e:
        print(f"[-] 登录请求失败: {e}")
        return False

    # 验证方式：访问一个普通板块，确认登录成功
    random_delay(2, 4)
    try:
        check = session.get(
            f"{FORUM_URL}/forum.php?mod=forumdisplay&fid=174",
            headers=get_headers(),
            timeout=30,
        )
        check.encoding = "utf-8"
    except requests.RequestException:
        print("[-] 登录验证请求失败")
        return False

    if "发表新帖" in check.text or "newthread" in check.text:
        print("[+] 登录成功！")
        return True

    # 备用验证
    if "欢迎" in resp.text or "succeedhandle" in resp.text:
        print("[+] 登录成功（通过登录响应确认）！")
        return True

    print("[-] 登录失败，请检查账号密码")
    return False


def search_fid183_threads(session: requests.Session, formhash: str) -> list[dict]:
    """
    通过搜索获取兼职信息发布栏(fid=183)中含关键词的帖子。
    注意：无法直接访问 fid=183（需水滴>0），但搜索可以搜到该板块帖子。
    """
    print(f"[*] 正在搜索「{KEYWORD}」...")

    try:
        resp = session.post(
            f"{FORUM_URL}/search.php?searchsubmit=yes",
            data={
                "mod": "forum",
                "formhash": formhash,
                "srchtype": "title",
                "srchtxt": KEYWORD,
                "searchsubmit": "yes",
            },
            headers=get_headers(referer=f"{FORUM_URL}/forum.php"),
            allow_redirects=True,
            timeout=30,
        )
        resp.encoding = "utf-8"
    except requests.RequestException as e:
        print(f"[-] 搜索请求失败: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    threads = []

    for li in soup.select("li.pbw"):
        tid = li.get("id", "")
        if not tid or not tid.isdigit():
            continue

        link = li.find("a", href=re.compile(r"tid=" + tid))
        title = link.get_text(strip=True) if link else ""

        # 检查板块来源是否为 fid=183
        forum_link = li.find("a", href=re.compile(r"forumdisplay.*?fid=183"))
        if not forum_link:
            continue

        # 提取时间
        spans = li.find_all("span")
        post_time = spans[0].get_text(strip=True) if spans else ""

        threads.append({
            "tid": tid,
            "title": title,
            "time": post_time,
            "url": f"{FORUM_URL}/forum.php?mod=viewthread&tid={tid}",
        })

    print(f"[*] 共找到 {len(threads)} 个来自兼职信息发布栏的帖子")
    return threads


def load_seen_ids() -> set:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, IOError) as e:
            print(f"[!] 读取缓存失败: {e}")
    return set()


def save_seen_ids(ids: set):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ids, key=int), f, ensure_ascii=False)


def filter_new(threads: list[dict], seen_ids: set) -> tuple[list[dict], set]:
    """过滤出新帖子并更新已见集合"""
    new = []
    updated = set(seen_ids)
    for t in threads:
        updated.add(t["tid"])
        if t["tid"] not in seen_ids:
            new.append(t)
    return new, updated


def send_email(threads: list[dict]):
    if not threads:
        return

    now = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    subject = f"[河畔监控] 发现 {len(threads)} 条家教兼职新帖"

    lines = [f"清水河畔论坛 - 家教兼职新帖通知", f"时间: {now}", f"共 {len(threads)} 条新帖", "", "-" * 40]
    for i, t in enumerate(threads, 1):
        lines.append(f"\n{i}. {t['title']}")
        lines.append(f"   时间: {t.get('time', '?')}")
        lines.append(f"   链接: {t['url']}")
    lines.extend(["", "-" * 40, "监控板块: 兼职信息发布栏 | 关键词: 家教 | 30分钟检查一次"])

    body = "\n".join(lines)
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = EMAIL_SENDER
    msg["To"] = EMAIL_RECIPIENT
    msg["Subject"] = Header(subject, "utf-8")

    try:
        print(f"[*] 发送邮件到 {EMAIL_RECIPIENT}...")
        server = smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30)
        server.login(EMAIL_SENDER, EMAIL_AUTH_CODE)
        server.sendmail(EMAIL_SENDER, [EMAIL_RECIPIENT], msg.as_string())
        server.quit()
        print("[+] 邮件发送成功！")
    except smtplib.SMTPAuthenticationError:
        print("[-] 邮件发送失败：QQ邮箱授权码错误")
    except smtplib.SMTPException as e:
        print(f"[-] 邮件发送失败: {e}")
    except Exception as e:
        print(f"[-] 未知错误: {e}")


def main():
    now = datetime.now(TZ)
    print("=" * 55)
    print(f"  清水河畔论坛监控 | {now.strftime('%Y-%m-%d %H:%M:%S')} (CST)")
    print("=" * 55)

    # 检查环境变量
    missing = [k for k, v in {
        "FORUM_USERNAME": FORUM_USERNAME,
        "FORUM_PASSWORD": FORUM_PASSWORD,
        "EMAIL_SENDER": EMAIL_SENDER,
        "EMAIL_AUTH_CODE": EMAIL_AUTH_CODE,
        "EMAIL_RECIPIENT": EMAIL_RECIPIENT,
    }.items() if not v]
    if missing:
        print(f"[-] 缺少配置: {', '.join(missing)}")
        return

    # 1. 登录
    session = requests.Session()
    if not login(session):
        return

    # 2. 获取 formhash（用于搜索）
    random_delay(2, 4)
    try:
        r = session.get(f"{FORUM_URL}/forum.php", headers=get_headers(), timeout=30)
        r.encoding = "utf-8"
    except requests.RequestException:
        print("[-] 获取 formhash 失败")
        return
    fh = re.search(r'formhash" value="([a-f0-9]+)"', r.text)
    if not fh:
        print("[-] 无法获取 formhash")
        return
    formhash = fh.group(1)

    # 3. 搜索帖子
    random_delay(2, 5)
    threads = search_fid183_threads(session, formhash)
    if not threads:
        print("[*] 未搜到来自兼职信息发布栏的帖子")
        return

    # 4. 过滤新帖
    seen_ids = load_seen_ids()
    new_threads, updated = filter_new(threads, seen_ids)
    save_seen_ids(updated)

    print(f"[*] 已追踪 {len(updated)} 个帖子")
    print(f"[*] 新增匹配: {len(new_threads)} 条")

    if new_threads:
        for t in new_threads:
            print(f"  [{t['tid']}] {t['title']}")
        send_email(new_threads)
    else:
        print("[*] 没有新的匹配帖子")

    print("[*] 监控完成")


if __name__ == "__main__":
    main()
