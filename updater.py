# updater.py — шаг 5: самообновление лаунчера через GitHub Releases.

import os
import sys
import subprocess
import requests

REPO = "MAKC180/flauncher"   # репозиторий лаунчера, в Releases лежат готовые .exe
EXE_NAME = "Flauncher.exe"   # как называется файл лаунчера в релизе


def is_exe():
    # True, если мы запущены как Flauncher.exe, и False, если как launcher.py.
    # PyInstaller (шаг 6) ставит в программе отметку sys.frozen.
    return getattr(sys, "frozen", False)


def version_tuple(text):
    # "v0.2" -> (0, 2), "1.10" -> (1, 10). Так версии правильно сравниваются:
    # (1, 10) > (1, 9), а строки "1.10" < "1.9" — неправильно.
    text = text.lstrip("vV")
    return tuple(int(part) for part in text.split(".") if part.isdigit())


def cleanup_old():
    # Удаляем старую версию, которая осталась после прошлого обновления
    old_path = sys.executable + ".old"
    if is_exe() and os.path.exists(old_path):
        try:
            os.remove(old_path)
        except OSError:
            pass   # не получилось — не страшно, удалим в следующий раз


def check_for_update(current_version):
    # Спрашиваем GitHub о последнем релизе.
    # Возвращаем (новая_версия, ссылка_на_exe) или None, если обновлять не нужно.
    url = "https://api.github.com/repos/" + REPO + "/releases/latest"
    response = requests.get(url, timeout=15)
    if response.status_code == 404:
        return None   # релизов ещё нет
    response.raise_for_status()
    release = response.json()

    latest_version = release["tag_name"]   # например "v0.2"
    if version_tuple(latest_version) <= version_tuple(current_version):
        return None   # у нас уже последняя версия

    # В релизе ищем файл Flauncher.exe
    for asset in release["assets"]:
        if asset["name"] == EXE_NAME:
            return latest_version, asset["browser_download_url"]
    return None


def install_update(download_url, callback):
    # Скачиваем новый .exe, ставим его на место старого и запускаем.
    exe_path = sys.executable            # путь к нашему Flauncher.exe
    new_path = exe_path + ".new"
    old_path = exe_path + ".old"

    # 1) Скачиваем кусками, чтобы двигать полоску загрузки
    with requests.get(download_url, stream=True, timeout=60) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        callback["setMax"](max(total, 1))
        done = 0
        with open(new_path, "wb") as f:
            for chunk in response.iter_content(256 * 1024):
                f.write(chunk)
                done += len(chunk)
                callback["setProgress"](done)

    if total and os.path.getsize(new_path) != total:
        os.remove(new_path)
        raise RuntimeError("Новая версия скачалась не полностью")

    # 2) Меняем файлы местами (запущенную программу можно переименовать, но не удалить)
    if os.path.exists(old_path):
        os.remove(old_path)
    os.rename(exe_path, old_path)
    os.rename(new_path, exe_path)

    # 3) Запускаем новую версию. Переменные окружения сбрасываем,
    #    чтобы новый .exe не перепутал свои файлы со старым (особенность PyInstaller).
    env = os.environ.copy()
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env.pop("_MEIPASS2", None)
    subprocess.Popen([exe_path], env=env)
