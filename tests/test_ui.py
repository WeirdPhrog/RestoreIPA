"""Окно загружается без ошибок QML и основные действия не падают. Без сети, Apple ID и показа окна.
python -m unittest discover tests"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

from PySide6.QtCore import QtMsgType, qInstallMessageHandler  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from restore_gui import app as gui  # noqa: E402

MESSAGES = []


def _collect(kind, _ctx, text):
    if kind != QtMsgType.QtDebugMsg:
        MESSAGES.append(text)


class Window(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qInstallMessageHandler(_collect)
        cls.qapp = QApplication.instance() or QApplication([])
        cls.engine, cls.backend = gui.create(cls.qapp, background=False, visible=False)

    @classmethod
    def tearDownClass(cls):
        qInstallMessageHandler(None)

    def pump(self):
        for _ in range(5):
            self.qapp.processEvents()

    def test_loads_without_qml_warnings(self):
        self.assertTrue(self.engine.rootObjects(), "Main.qml не загрузился")
        self.pump()
        self.assertEqual(MESSAGES, [], "предупреждения QML:\n" + "\n".join(MESSAGES))

    def test_pages_search_and_selection(self):
        b = self.backend
        for page in ("apps", "mine", "ipa", "home"):
            b.showPage(page)
            self.assertEqual(b.page, page)
        b.setQuery("сбер")
        self.assertGreater(b.apps.rowCount(), 0)
        b.setCategory("Банки")
        self.assertTrue(all(a.get("cat") == "Банки" for a in b.apps.items))
        b.setCategory("Все")
        b.setQuery("")
        self.assertEqual(b.apps.rowCount(), len(b.catalog.apps))
        b.toggleSelected("popular", 0)
        self.assertEqual(b.selectedCount, 1)
        self.assertTrue(b.popular.rows[0]["selected"])
        b.clearSelection()
        self.assertEqual(b.selectedCount, 0)
        self.pump()
        self.assertEqual(MESSAGES, [], "\n".join(MESSAGES))

    def test_logout_and_close_erase_email(self):
        import tempfile
        from restore_gui import storage
        b = self.backend
        with tempfile.TemporaryDirectory() as d:
            old, storage.EMAIL = storage.EMAIL, Path(d) / "email.txt"
            wipe = b.wipe_session
            b.wipe_session = lambda revoke=True: False  # настоящую сессию пользователя не трогаем
            try:
                for action in (b.logout, b.shutdown):
                    storage.EMAIL.write_text("someone@example.com", encoding="utf-8")
                    b.loggedIn, b.account = True, "someone@example.com"
                    action()
                    self.assertFalse(storage.EMAIL.exists(), f"{action.__name__} не стёр email")
                    if action == b.logout:
                        self.assertEqual(b.account, "", "после «Выйти» email остался на экране")
            finally:
                storage.EMAIL = old
                b.wipe_session = wipe

    def test_scan_finds_keep_purchases_and_page(self):
        """Находка «Найти мои удалённые» добавляется к покупкам и не переключает страницу (ошибка 1.0.1)."""
        b = self.backend
        saved = b.purchases, b.avail, b.owned_state.copy()
        try:
            b.on_purchases_result({1: "Банк", 2: "Магазин"}, set())
            b.showPage("apps")
            b.add_found({3: "Ещё банк"})
            b.add_found({3: "Ещё банк"})  # повтор не дублирует
            self.assertEqual(b.page, "apps")
            self.assertEqual([a["id"] for a in b.purchases], [1, 3, 2])  # по названию
            self.assertTrue(b.owned_state[3])
            self.assertEqual(b.mine.rowCount(), 3)
        finally:
            b.purchases, b.avail = saved[0], saved[1]
            b.owned_state.clear()
            b.owned_state.update(saved[2])
            b.refresh_mine()
            b.showPage("home")

    def test_stop_interrupts_running_command(self):
        """«Остановить» завершает процесс (раньше скачивание продолжалось до конца)."""
        import threading
        import time
        b = self.backend
        b.stop_flag = False
        result = {}
        cmd = [sys.executable, "-c", "import time; print('start', flush=True); time.sleep(60)"]
        t = threading.Thread(target=lambda: result.update(r=b.stream(cmd)))
        t0 = time.time()
        t.start()
        time.sleep(1)
        b.on_stop()
        t.join(timeout=15)
        self.assertFalse(t.is_alive(), "stream не вернулся после «Остановить»")
        self.assertLess(time.time() - t0, 15)
        self.assertNotEqual(result["r"][0], 0)
        b.stop_flag = False

    def test_no_new_command_after_stop(self):
        """«Остановить» между командами (например, во время проверки iPhone) — следующая не запускается."""
        import tempfile
        b = self.backend
        with tempfile.TemporaryDirectory() as d:
            marker = Path(d) / "ran"
            b.stop_flag = True
            try:
                rc, _ = b.stream([sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"])
            finally:
                b.stop_flag = False
            self.assertNotEqual(rc, 0)
            self.assertFalse(marker.exists(), "команда запустилась после «Остановить»")

    def test_pick_compatible_removes_probe_downloads(self):
        """Подбор версии оставляет в архиве только подошедшую версию, пробные IPA удаляет."""
        import tempfile
        import restore_apps as core
        from restore_gui import tasks
        b = self.backend
        b.stop_flag = False
        with tempfile.TemporaryDirectory() as d:
            old, core.OUT_DIR = core.OUT_DIR, Path(d)
            saved = b.task_download, tasks.ipa_info

            def download(ipatool, app_id, name, purchase, version=None, fallback=False):
                p = Path(d) / f"x_{app_id}_v{version}.ipa"
                p.write_bytes(b"ipa")
                return p

            # версии 3 и новее требуют iOS 18, старые — iOS 15; на телефоне iOS 16
            b.task_download = download
            tasks.ipa_info = lambda p: {"MinimumOSVersion": "18.0" if int(p.stem.split("_v")[1]) >= 3 else "15.0"}
            try:
                best = b.pick_compatible("ipatool", 1, "x", ["5", "4", "3", "2", "1"], "16.0")
                self.assertEqual(best.name, "x_1_v2.ipa")
                self.assertEqual(sorted(p.name for p in Path(d).glob("*.ipa")), ["x_1_v2.ipa"])
            finally:
                core.OUT_DIR = old
                b.task_download, tasks.ipa_info = saved

    def test_login_asks_2fa_even_when_ipatool_exits_0(self):
        """ipatool 2.7: «2FA code is required…» с кодом возврата 0 — это не вход, нужно спросить код (ошибка 1.0.0)."""
        from restore_gui import tasks
        b = self.backend
        need = (0, "INF preparing authentification\nINF 2FA code is required; run the command again and supply "
                   "a code using the '--auth-code' flag\n")
        saved = (b.stream, b.ask_main, b.session_ok, b.post, tasks.sap_ready, b.loggedIn, b.account)
        b.post, tasks.sap_ready = (lambda fn: fn()), (lambda: True)
        try:
            for answers, code, session, expected in (
                    ([need, (0, "INF success=true\n")], "123456", True, True),   # код введён — вход
                    ([need], None, True, False),                                  # код не введён — не вход
                    ([need, need], "000000", True, False),                        # код не подошёл
                    ([(0, "INF success=true\n")], None, False, False)):           # «успех» без сессии
                calls = iter(answers)
                b.stream = lambda cmd, quiet_json=False: next(calls)
                b.ask_main = lambda title, prompt: code
                b.session_ok = lambda ipatool: session
                b.loggedIn, b.account = False, ""
                b.task_login("ipatool", "someone@example.com", "pw")
                self.assertEqual(b.loggedIn, expected, f"{answers[0][1][-40:]!r}, код {code}, сессия {session}")
        finally:
            b.stream, b.ask_main, b.session_ok, b.post, tasks.sap_ready, b.loggedIn, b.account = saved

    def test_download_creates_missing_parent_folders(self):
        """У установленной версии папка — Документы\\Restore\\ipa; если нет даже Restore, скачивание не падает."""
        import tempfile
        import restore_apps as core
        b = self.backend
        with tempfile.TemporaryDirectory() as d:
            old, core.OUT_DIR = core.OUT_DIR, Path(d) / "Documents" / "Restore" / "ipa"
            stream, b.stream = b.stream, lambda cmd, quiet_json=False: (1, "")  # ipatool не запускаем
            try:
                self.assertIsNone(b.task_download("ipatool", 492224193, "Сбербанк", False))
                self.assertTrue(core.OUT_DIR.is_dir())
            finally:
                core.OUT_DIR, b.stream = old, stream

    def test_dialogs_open(self):
        b = self.backend
        b.openLogin("")
        self.assertTrue(b.loginOpen)
        b.loginOpen = False
        self.assertIn("Компоненты", b.helpText())
        self.pump()
        self.assertEqual(MESSAGES, [], "\n".join(MESSAGES))


if __name__ == "__main__":
    unittest.main()
