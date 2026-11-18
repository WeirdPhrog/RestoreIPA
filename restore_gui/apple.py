"""ipatool и сервера Apple: запуск команд, разбор ответов, понятные объяснения ошибок. Без интерфейса."""
import base64
import contextlib
import hashlib
import http.client
import json
import os
import queue
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import restore_apps as core

# временные файлы ipatool, запущенного окном (core.ipatool_env), — отдельно от консольной версии: остатки здесь
# окно удаляет (clear_ipatool_tmp), видя только свои процессы. Второй экземпляр окна не запускается
core.IPATOOL_TMP = core.DATA_DIR / "ipatool-tmp"

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")  # цветовые коды ipatool в журнале не нужны
NOT_OWNED = re.compile(r"license is required", re.I)  # Apple: приложения нет в истории этого Apple ID
SESSION_LOST = re.compile(r"failed to get account|keyring|not logged|sign.?in|password token|not provisioned", re.I)
# копии сессии для параллельных запросов (in_session_copies); до 1.2.0 — %TEMP%\restore_scan_*
SCAN_ROOT = core.DATA_DIR / "scan"
UNICORN_VERSION = "2.1.4"
UNICORN_SHA256 = "d8f9a89222ffa74493a1d47090e17f8e1db8ac171a3128c6a76a4ea09de11469"  # как в исходниках ipatool
# служебные файлы Apple для входа в кэше ipatool (sap/…): папка ipatool 2.8 и папки ipatool 2.7, которые больше
# не нужны (ipatool 2.7 перестал входить в Apple ID: Apple сменил способ входа)
SAP_ASSETS = "apple-assets-macos-27-26A434-arm64-v5"
SAP_OLD = ("apple-assets-v2", "apple-storeagent-v1")
# файлы в SAP_ASSETS (requiredFiles в internal/sap/assets/assets.go ipatool 2.8.1). ipatool пишет каждый во временный
# .download-* и переименовывает, так что файл с этим именем — целый
SAP_FILES = ("AppleMediaServices", "commerce", "adid", "CoreFP", "CoreFP.icxs", "fairplayd", "FairPlayIOKit")
# адреса, без которых не работает вход/скачивание (для «Проверить связь с Apple»). Адреса подписи входа
# (fpinit, s.mzstatic.com) ipatool берёт из настроек магазина: sign-sap-setup и sign-sap-setup-cert в bag.xml
APPLE_HOSTS = (
    ("buy.itunes.apple.com", "вход и лицензии"),
    ("p25-buy.itunes.apple.com", "покупки и версии"),
    ("init.itunes.apple.com", "настройки магазина"),
    ("fpinit.itunes.apple.com", "подпись входа"),
    ("s.mzstatic.com", "сертификат для подписи входа"),
    ("auth.itunes.apple.com", "вход в Apple ID"),
    ("gsa.apple.com", "подготовка входа на этом компьютере"),
    ("www.apple.com", "корневой сертификат Apple для входа"),
    ("downloaddispatch.itunes.apple.com", "ссылки на скачивание"),
    ("iosapps.itunes.apple.com", "скачивание IPA"),
    ("itunes.apple.com", "поиск и наличие в App Store"),
    ("updates.cdn-apple.com", "служебные файлы для первого входа"),
    ("wkms-public.apple.com", "ключ к служебным файлам для первого входа"),
    ("github.com", "резерв для эмулятора (если встроенный не подошёл)"),
)
# сертификат этих адресов выдан от корневого сертификата Apple, которого нет среди системных: ipatool скачивает его
# с www.apple.com и доверяет ему вместе с системными. Проверка связи делает так же (apple_root_context)
APPLE_OWN_CA = {"gsa.apple.com"}
APPLE_ROOT_URL = "https://www.apple.com/appleca/AppleIncRootCertificate.cer"  # как в ipatool
# «Apple Root CA» (Apple Inc., 2006–2035): SHA-256 опубликован на apple.com/certificateauthority
APPLE_ROOT_SHA256 = "b0b1730ecbc7ff4505142c49f1295e6eda6bcaed7e2c68c5be91b5a11001f024"


def env_proxy(host):
    """Прокси для https://host так, как его выбирает ipatool (Go, http.ProxyFromEnvironment): только HTTPS_PROXY
    с исключениями из NO_PROXY. Системный прокси Windows Go не читает — его не берём и мы. None — напрямую."""
    proxies = urllib.request.getproxies_environment()
    proxy = proxies.get("https")
    if not proxy or urllib.request.proxy_bypass_environment(host, proxies):
        return None
    return proxy if "://" in proxy else "http://" + proxy  # Go тоже считает адрес без схемы http-прокси


def proxy_label(proxy):
    """Адрес прокси для журнала — без логина и пароля."""
    p = urllib.parse.urlsplit(proxy)
    return f"{p.scheme}://{p.hostname}" + (f":{p.port}" if p.port else "")


def env_urlopen(req, timeout=10):
    """urlopen тем же путём, что у ipatool: прокси только из переменных окружения (env_proxy), без системного."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(urllib.request.getproxies_environment()))
    return opener.open(req, timeout=timeout)


def apple_root_context(timeout=10):
    """SSL-контекст с системными корневыми сертификатами и «Apple Root CA» — как у ipatool для gsa.apple.com.

    Сертификат скачивается с www.apple.com и сверяется по SHA-256. Ошибки: OSError (нет связи, перехват HTTPS),
    ValueError (скачалось не то)."""
    with env_urlopen(APPLE_ROOT_URL, timeout=timeout) as r:
        der = r.read(64 << 10)
    if hashlib.sha256(der).hexdigest() != APPLE_ROOT_SHA256:
        raise ValueError("с www.apple.com скачался не тот корневой сертификат Apple")
    ctx = ssl.create_default_context()
    ctx.load_verify_locations(cadata=der)
    return ctx


def tls_check(host, ctx, timeout=10):
    """Защищённое соединение с host:443 с проверкой сертификата по ctx — напрямую или, как ipatool, через прокси
    из HTTPS_PROXY (CONNECT). Ошибка — исключение (OSError, ssl.SSLError): перехват HTTPS антивирусом даёт
    SSLCertVerificationError, отказ прокси — OSError «Tunnel connection failed»."""
    proxy = env_proxy(host)
    if not proxy:
        with socket.create_connection((host, 443), timeout=timeout) as sock, \
                ctx.wrap_socket(sock, server_hostname=host):
            return
    p = urllib.parse.urlsplit(proxy)
    if p.scheme != "http" or not p.hostname:
        raise OSError(f"прокси {proxy_label(proxy)} из HTTPS_PROXY эта проверка не поддерживает (только http://)")
    headers = {}
    if p.username:
        cred = f"{urllib.parse.unquote(p.username)}:{urllib.parse.unquote(p.password or '')}"
        headers["Proxy-Authorization"] = "Basic " + base64.b64encode(cred.encode()).decode()
    conn = http.client.HTTPSConnection(p.hostname, p.port or 80, timeout=timeout, context=ctx)
    conn.set_tunnel(host, 443, headers)
    try:
        conn.connect()  # CONNECT через прокси, затем TLS с host и проверкой сертификата по ctx
    finally:
        conn.close()


def adopt_old_cache():
    """Перенести кэш ipatool версий до 1.2.0 из %LOCALAPPDATA%\\ipatool в папку программы (core.IPATOOL_CACHE).

    Переносится Unicorn и только если в папке программы его ещё нет; служебные файлы Apple оттуда — от ipatool 2.7,
    нынешнему не нужны. Пустая старая папка удаляется.
    """
    if not sys.platform.startswith("win"):
        return False
    old = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ipatool"
    if old == core.IPATOOL_CACHE:
        return False
    moved = False
    for name in ("unicorn",):
        src, dest = old / name, core.IPATOOL_CACHE / name
        if src.is_dir() and not dest.exists():
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                src.rename(dest)  # тот же диск: мгновенно, без копирования
                moved = True
            except OSError:
                pass  # занято или нет прав — ipatool скачает заново
    with contextlib.suppress(OSError):
        old.rmdir()  # только пустую: если там что-то ещё, это не наше
    return moved


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def seed_unicorn():
    """Положить встроенный libunicorn.dll в кэш ipatool, чтобы первый вход не качал его с GitHub.

    Это тот же официальный файл Unicorn 2.1.4, который ipatool скачивает сам; хеш сверяется.
    Возвращает True, если файл был положен сейчас.
    """
    if not sys.platform.startswith("win"):
        return False
    src = bundled("libunicorn.dll")
    if not src:
        return False
    dest = core.IPATOOL_CACHE / "unicorn" / UNICORN_VERSION / UNICORN_SHA256 / "libunicorn.dll"
    try:
        if dest.exists() and sha256_file(dest) == UNICORN_SHA256:
            return False
        if sha256_file(src) != UNICORN_SHA256:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return True
    except OSError:
        return False


def sap_ready():
    """Скачаны ли уже служебные файлы Apple, нужные ipatool для входа: все SAP_FILES. После прерванной первой
    загрузки в папке остаются только недописанные .download-* — это ещё не готово."""
    d = core.IPATOOL_CACHE / "sap" / SAP_ASSETS
    try:
        return all((d / name).is_file() for name in SAP_FILES)
    except OSError:
        return False


def drop_old_sap():
    """Удалить служебные файлы Apple от прежних версий ipatool (SAP_OLD) из кэша в папке программы.
    Возвращает True, если что-то удалено."""
    dropped = False
    for name in SAP_OLD:
        d = core.IPATOOL_CACHE / "sap" / name
        if d.is_dir() and not d.is_symlink():
            shutil.rmtree(d, ignore_errors=True)
            dropped = dropped or not d.exists()
    return dropped


# ошибки, после которых следующие запросы к Apple упадут так же (пакетная установка на них прерывается)
NET_ERROR = (r"no such host|getaddrinfo|dial tcp|i/o timeout|connection (reset|refused)|network is unreachable"
             r"|unexpected EOF|\": EOF\b")
ACCOUNT_LOCKED = r"account (is )?(disabled|locked)|has been (disabled|locked)"
NO_SESSION = r"password token expired|sign.?in required|failed to get account|not logged|not provisioned"

HINTS = (
    (NET_ERROR,
     "Нет связи с серверами Apple. Частая причина — DNS «для обхода блокировок» (Comss, GeoHide и т.п.), он "
     "подменяет адреса Apple: верните автоматический DNS в настройках сети. Также мешают записи в файле hosts. "
     "Нажмите «Проверить связь с Apple» слева."),
    (r"x509|certificate|tls: ",
     "Защищённое соединение с Apple перехватывается. Обычно это антивирус с проверкой HTTPS (Касперский, ESET, "
     "Dr.Web) — отключите в нём проверку защищённых соединений или добавьте программу в исключения."),
    (ACCOUNT_LOCKED,
     "Apple заблокировал этот Apple ID. Пароль ни при чём: восстановите доступ на iforgot.apple.com."),
    # «Your Apple ID or password was entered incorrectly.» — но не любое «incorrect» в выводе
    (r"invalid credentials|bad.?credentials|(apple id|password|credentials)[^\n]{0,40}incorrect|MZFinance.BadLogin",
     "Неверный Apple ID или пароль. Если окно с кодом на iPhone не появилось — почти наверняка ошибка в пароле."),
    (r"\b429\b|rate.?limit|too many",  # \b: не цифры внутри App ID или размера файла
     "Apple временно ограничил попытки. НЕ повторяйте вход подряд: подождите около часа и попробуйте один раз."),
    (r"license is required|license.*not found",
     "Этого приложения нет в истории покупок вашего Apple ID — Apple его не отдаст. Войдите под тем Apple ID, "
     "под которым его когда-то скачивали."),
    (NO_SESSION,
     "Сессия входа истекла или вход не выполнен. Нажмите «Войти»."),
    (r"mac address", "ipatool не нашёл сетевой адаптер (нужен для подписи запросов). Включите сетевой адаптер."),
    # не просто «locked»: иначе «account is locked» даёт ещё и совет разблокировать iPhone
    (r"DeviceLocked|PasswordProtected|(device|phone) (is )?locked",
     "iPhone заблокирован. Разблокируйте его и держите экран включённым во время установки."),
    (r"No ?Device|device not found|not connected|ConnectionFailed|usbmux",
     "iPhone не найден. Подключите кабелем, нажмите «Доверять» и введите код-пароль. На Windows нужен iTunes "
     "или «Устройства Apple» из Microsoft Store (это драйвер USB)."),
    (r"MinimumOSVersion|DeviceOSVersionTooLow|requires iOS",
     "Эта версия приложения не подходит для iOS на вашем iPhone — выберите «Автоматически» в окне приложения."),
)


def explain_error(text):
    seen = []
    for pat, hint in HINTS:
        if re.search(pat, text or "", re.I) and hint not in seen:
            seen.append(hint)
    return seen


def fatal_error(text):
    """Нет связи, нет сессии или аккаунт заблокирован: следующие приложения пакета упадут так же."""
    return any(re.search(p, text or "", re.I) for p in (NET_ERROR, ACCOUNT_LOCKED, NO_SESSION))


def bundled(name):
    """Путь к инструменту, вшитому в exe (или лежащему рядом с программой)."""
    for base in (core.BUNDLE, core.HERE):
        for n in (name + ".exe", name):
            p = base / n
            if p.is_file():
                return str(p)
    return None


def extract_apps(obj, acc):
    """Достаёт пары (id, название) из JSON-ответа `ipatool list-purchases`.

    ipatool (с 2.7) печатает элементы как {"id":…, "bundleID":…, "name":…, "version":…};
    ключи сверяются без учёта регистра, на случай других версий.
    """
    if isinstance(obj, dict):
        low = {str(k).lower(): v for k, v in obj.items()}
        i = next((low[k] for k in ("id", "trackid", "appid", "adamid", "app_id") if k in low), None)
        n = next((low[k] for k in ("name", "trackname", "appname", "title") if k in low), None)
        if i is not None and n is not None and str(i).isdigit():
            acc[int(i)] = str(n)
        for v in obj.values():
            extract_apps(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            extract_apps(v, acc)


def parse_purchases(text):
    """Из вывода ipatool: (найденные приложения {id: имя}, totalCount или None)."""
    acc, total = {}, None
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            obj = json.loads(ln)
        except ValueError:
            continue
        extract_apps(obj, acc)
        if isinstance(obj, dict):
            low = {str(k).lower(): v for k, v in obj.items()}
            if isinstance(low.get("totalcount"), int):
                total = low["totalcount"]
    return acc, total


def fetch_available(ids, country, chunk=100):
    """ID, которые сейчас есть в App Store страны `country` (iTunes Lookup).

    Возвращает множество; None, если проверить не удалось (нет сети и т. п.).
    """
    ids = list(ids)
    found = set()
    for i in range(0, len(ids), chunk):
        part = ids[i:i + chunk]
        url = f"https://itunes.apple.com/lookup?country={country}&id=" + ",".join(map(str, part))
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.load(r)
        except (urllib.error.URLError, ValueError, OSError):
            return None
        for x in data.get("results", []):
            if x.get("trackId") is not None:
                found.add(int(x["trackId"]))
    return found


# запущенные ipatool и установщик: {процесс: метка}. Без этого «Остановить» не может прервать скачивание,
# а после закрытия окна процессы остаются работать (на Windows дочерний процесс не завершается с родителем).
_PROCS, _PROCS_LOCK = {}, threading.Lock()
_CLOSED = False  # программа закрывается: новых процессов не запускаем (кроме уборки сессии, tag="wipe")


class ProcsClosed(OSError):
    """Программа закрывается — процесс не запущен."""


def popen(cmd, tag="", **kw):
    """subprocess.Popen с учётом в _PROCS; tag="task" — основная операция (её прерывает «Остановить»).

    Запуск и учёт — под одной блокировкой: kill_procs не может проскочить между ними, а после close_procs
    поток, ещё не узнавший о закрытии, не запустит ipatool, переживающий программу."""
    with _PROCS_LOCK:
        if _CLOSED and tag != "wipe":
            raise ProcsClosed("программа закрывается")
        p = subprocess.Popen(cmd, creationflags=NO_WINDOW, **kw)
        _PROCS[p] = tag
    return p


def forget(p):
    with _PROCS_LOCK:
        _PROCS.pop(p, None)


def kill_procs(tag=None):
    """Завершить запущенные процессы: с меткой tag или все (tag=None — при закрытии программы)."""
    with _PROCS_LOCK:
        procs = [p for p, t in _PROCS.items() if tag is None or t == tag]
    for p in procs:
        try:
            p.kill()
        except OSError:
            pass  # уже завершился


def close_procs():
    """При закрытии программы: завершить все процессы и не запускать новых (кроме tag="wipe")."""
    global _CLOSED
    with _PROCS_LOCK:
        _CLOSED = True
    kill_procs()


def clear_ipatool_tmp(wait=0.0):
    """Удалить временные файлы ipatool (core.IPATOOL_TMP), если сейчас не работает ни один запущенный процесс, —
    при запуске и закрытии программы. wait — сколько секунд ждать, пока завершатся уже прерванные процессы
    (при закрытии: close_procs их только прерывает). Под _PROCS_LOCK: пока папка удаляется, popen не запустит
    новый ipatool. Возвращает True, если папка удалена."""
    if wait:
        with _PROCS_LOCK:
            procs = list(_PROCS)
        deadline = time.monotonic() + wait
        for p in procs:
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(max(0.0, deadline - time.monotonic()))
    with _PROCS_LOCK:
        if any(p.poll() is None for p in _PROCS) or not core.IPATOOL_TMP.is_dir():
            return False
        shutil.rmtree(core.IPATOOL_TMP, ignore_errors=True)
        return not core.IPATOOL_TMP.exists()


def clear_stale_locks():
    """Удалить *.lock в основной сессии. Звать, только когда в ней точно не работает ipatool (под dlock):
    lock-файлы остаются от убитых процессов — после «Остановить» или аварийного завершения."""
    for stale in (core.IPATOOL_HOME / ".ipatool").glob("*.lock"):
        try:
            stale.unlink()
        except OSError:
            pass


def run_quiet(cmd, timeout=90, env=None, tag=""):
    """Запуск без построчного журнала: (код возврата, весь вывод без цветовых кодов)."""
    try:
        p = popen(cmd, tag=tag, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                  encoding="utf-8", errors="replace",
                  env=env if env is not None else (core.ipatool_env() if core.is_ipatool(cmd) else None))
    except ProcsClosed:
        return 1, "программа закрывается"
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        return 124, "timeout"
    finally:
        forget(p)
    return p.returncode, ANSI.sub("", (out or "") + (err or ""))


def in_session_copies(items, fn, workers, lock=None):
    """fn(item, env) для каждого item в нескольких потоках: запрос к Apple ждёт сеть 1–2 с, так быстрее.

    ipatool хранит сессию и cookies в ~/.ipatool, и параллельные процессы портят общие файлы (падение
    с panic), поэтому у каждого потока своя копия сессии во временной папке внутри SCAN_ROOT; env — окружение
    с этой папкой. Копии удаляются в конце (и при следующем запуске программы, если она упала — см. wipe_session).
    lock — блокировка основной сессии: копируем её, только когда ipatool в неё не пишет.
    """
    items = list(items)
    if not items:
        return
    n = max(1, min(workers, len(items)))
    src_dir = core.IPATOOL_HOME / ".ipatool"
    SCAN_ROOT.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(dir=SCAN_ROOT))
    envs = queue.Queue()
    try:
        with lock or contextlib.nullcontext():
            clear_stale_locks()  # остались от аварийно завершённых процессов
            for w in range(n):
                h = root / f"w{w}"
                h.mkdir()
                if src_dir.exists():
                    shutil.copytree(src_dir, h / ".ipatool", ignore=shutil.ignore_patterns("*.lock"))
                envs.put(core.ipatool_env(h))

        def one(item):
            env = envs.get()
            try:
                fn(item, env)
            finally:
                envs.put(env)

        with ThreadPoolExecutor(max_workers=n) as pool:
            list(pool.map(one, items))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def scan_copies():
    """Папки с копиями сессии от in_session_copies, в том числе оставшиеся после сбоя (и от версий до 1.2.0)."""
    found = list(SCAN_ROOT.glob("*")) if SCAN_ROOT.is_dir() else []
    return found + list(Path(tempfile.gettempdir()).glob("restore_scan_*"))


def list_versions_cmd(ipatool, kc, app_id):
    """Проба владения: ничего не скачивает, а Apple отвечает «license is required», если приложения нет в истории."""
    return [ipatool, *kc, "list-versions", "-i", str(app_id), "--format", "json"]


def ownership(rc, out):
    """Ответ list-versions: True — приложение есть в истории Apple ID, False — нет, None — понять нельзя."""
    if rc == 0:
        return True
    return False if NOT_OWNED.search(out) else None


def parse_versions(out):
    """ID версий из ответа list-versions, новые первыми."""
    versions = set()
    for ln in out.splitlines():
        ln = ln.strip()
        if not ln.startswith("{"):
            continue
        try:
            obj = json.loads(ln)
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("externalVersionIdentifiers"), list):
            versions.update(str(x) for x in obj["externalVersionIdentifiers"])
    return sorted(versions, key=lambda x: int(x) if x.isdigit() else 0, reverse=True)


def clip(text, limit=700, head=450, tail=250):
    """Длинный ответ ipatool для журнала: начало и конец; пустой — «(пустой ответ)»."""
    t = (text or "").strip()
    return (t if len(t) <= limit else t[:head] + "\n …\n" + t[-tail:]) or "(пустой ответ)"
