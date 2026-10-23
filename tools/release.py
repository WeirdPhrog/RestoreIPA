"""Версия программы и описание выпуска — для сборки и GitHub Actions (.github/workflows/release.yml).

Версия одна на весь проект: restore_apps.__version__, в формате X.Y.Z (SemVer). Выпуск — тег vX.Y.Z;
описание выпуска берётся из раздела «## X.Y.Z» в CHANGELOG.md.

  python tools/release.py version          -> 1.1.0
  python tools/release.py check v1.1.0     -> ошибка, если тег не совпадает с версией или нет раздела в CHANGELOG
  python tools/release.py notes 1.1.0      -> текст раздела «## 1.1.0» из CHANGELOG.md
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"\d+\.\d+\.\d+")


def version():
    """restore_apps.__version__ — без импорта модуля (он при импорте создаёт папки)."""
    text = (ROOT / "restore_apps.py").read_text(encoding="utf-8")
    return re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M).group(1)


def notes(ver):
    """Текст раздела «## <ver>» из CHANGELOG.md (до следующего «## »); None, если раздела нет."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## {re.escape(ver)}\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else None


def check(tag):
    """Список ошибок выпуска по тегу (пустой — можно выпускать)."""
    ver, errors = version(), []
    if not SEMVER.fullmatch(ver):
        errors.append(f"версия «{ver}» в restore_apps.py не в формате X.Y.Z")
    if tag != f"v{ver}":
        errors.append(f"тег {tag} не совпадает с версией в restore_apps.py — ожидался v{ver}")
    if not notes(ver):
        errors.append(f"в CHANGELOG.md нет раздела «## {ver}» с описанием выпуска")
    return errors


def main(argv):
    cmd, *args = argv or ["version"]
    if cmd == "version":
        print(version())
    elif cmd == "check":
        errors = check(args[0])
        for e in errors:
            print("Ошибка выпуска:", e, file=sys.stderr)
        return 1 if errors else 0
    elif cmd == "notes":
        text = notes(args[0])
        if text is None:
            print(f"В CHANGELOG.md нет раздела «## {args[0]}»", file=sys.stderr)
            return 1
        sys.stdout.reconfigure(encoding="utf-8")
        print(text)
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
