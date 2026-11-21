# pack_sync.py — шаг 3: приводим папку игры в точное соответствие со сборкой на GitHub.

import os
import hashlib
import urllib.parse
import requests   # библиотека для скачивания из интернета

REPO = "MAKC180/flauncher-pack"   # репозиторий сборки на GitHub


def sha1_of_file(path):
    # Тот же «отпечаток», что считает build_modpack.py
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def get_latest_commit():
    # Спрашиваем у GitHub номер самого свежего «снимка» (commit) ветки main.
    # Этот ответ GitHub не кэширует надолго, поэтому номер всегда свежий.
    url = "https://api.github.com/repos/" + REPO + "/commits/main"
    response = requests.get(url, headers={"Accept": "application/vnd.github.sha"}, timeout=30)
    response.raise_for_status()
    return response.text.strip()


def get_modpack():
    # 1) Узнаём номер свежего снимка
    commit = get_latest_commit()
    # 2) Все файлы берём именно из этого снимка — они никогда не меняются,
    #    поэтому старые копии из кэша GitHub нам не помешают
    base_url = "https://raw.githubusercontent.com/" + REPO + "/" + commit + "/"
    response = requests.get(base_url + "modpack.json", timeout=30)
    response.raise_for_status()   # если GitHub ответил ошибкой — остановиться
    modpack = response.json()
    modpack["base_url"] = base_url   # запоминаем, откуда качать файлы
    return modpack


def get_players(modpack):
    # Скачиваем список игроков players.json из того же «снимка», что и сборку
    response = requests.get(modpack["base_url"] + "players.json", timeout=30)
    if response.status_code == 404:
        raise RuntimeError("Список игроков не найден на GitHub")
    response.raise_for_status()
    return response.json()["players"]


def download_file(base_url, rel_path, local_path):
    # quote() заменяет пробелы и спецсимволы в имени файла на %20 и т.п., чтобы ссылка работала
    url = base_url + urllib.parse.quote(rel_path)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    with open(local_path, "wb") as f:
        f.write(response.content)


def do_nothing(*args):
    # Пустая функция — если нам не передали, кому сообщать о прогрессе
    pass


def sync_pack(game_dir, modpack, callback):
    # callback — словарь функций, как у minecraft_launcher_lib:
    #   "setStatus"   — сообщить текст («Скачиваю ...»)
    #   "setMax"      — сколько всего шагов (для полоски загрузки)
    #   "setProgress" — сколько шагов уже сделано
    status = callback.get("setStatus", do_nothing)
    set_max = callback.get("setMax", do_nothing)
    set_progress = callback.get("setProgress", do_nothing)

    files = modpack["files"]
    set_max(len(files))
    status("Проверяю моды")

    # 1) Скачиваем то, чего нет или что изменилось
    for number, file in enumerate(files, start=1):
        set_progress(number)
        local_path = os.path.join(game_dir, file["path"])
        if os.path.exists(local_path) and sha1_of_file(local_path) == file["sha1"]:
            continue   # файл уже такой, как нужно — пропускаем
        status(f"[{number}/{len(files)}] Скачиваю {file['path']}")
        # До 3 попыток: иногда интернет или GitHub отдают файл с ошибкой
        for attempt in range(3):
            try:
                download_file(modpack["base_url"], file["path"], local_path)
                if sha1_of_file(local_path) == file["sha1"]:
                    break   # файл правильный — выходим из цикла попыток
            except requests.RequestException:
                pass        # ошибка сети — просто пробуем ещё раз
        else:
            # сюда попадаем, только если все 3 попытки не удались
            raise RuntimeError("Не удалось скачать файл: " + file["path"])

    # 2) Удаляем из mods моды, которых больше нет в сборке
    #    (папку config не чистим: моды сами создают там свои файлы)
    needed = set(file["path"] for file in files)
    mods_dir = os.path.join(game_dir, "mods")
    if os.path.isdir(mods_dir):
        for name in os.listdir(mods_dir):
            if "mods/" + name not in needed:
                status("Удаляю лишний мод " + name)
                os.remove(os.path.join(mods_dir, name))

    status("Сборка актуальна")
