# accounts.py — аккаунты игроков: вход, регистрация, смена пароля, удаление.
# Список лежит в отдельном репозитории MAKC180/flauncher-players (файл players.json).
# Пароли не хранятся — только «соль» и «отпечаток» (хэш) пароля.

import os
import sys
import json
import hmac        # безопасное сравнение отпечатков
import base64      # GitHub передаёт содержимое файлов в виде base64
import secrets     # случайная «соль»
import hashlib
import requests

REPO = "MAKC180/flauncher-players"
FILE_URL = "https://api.github.com/repos/" + REPO + "/contents/players.json"
TOKEN_FILE_NAME = "players_token.txt"   # ключ доступа к репозиторию аккаунтов
ITERATIONS = 200_000                    # сколько раз «перемешивать» пароль


def read_token():
    # В Flauncher.exe ключ лежит во временной папке программы (sys._MEIPASS),
    # а при запуске launcher.py — рядом с кодом.
    folder = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(folder, TOKEN_FILE_NAME), "r", encoding="utf-8") as f:
        return f.read().strip()


def github_headers():
    return {"Authorization": "Bearer " + read_token(),
            "Accept": "application/vnd.github+json"}


def hash_password(password, salt_hex):
    # PBKDF2 — специальный медленный способ сделать «отпечаток» пароля
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                 bytes.fromhex(salt_hex), ITERATIONS)
    return digest.hex()


def load_players():
    # Скачиваем players.json через GitHub API.
    # sha — номер версии файла: он нужен, чтобы потом сохранить изменения.
    response = requests.get(FILE_URL, headers=github_headers(), timeout=30)
    response.raise_for_status()
    data = response.json()
    text = base64.b64decode(data["content"]).decode("utf-8")
    return json.loads(text)["players"], data["sha"]


def save_players(players, sha, message):
    # Отправляем новый players.json. Возвращаем код ответа GitHub:
    # 200 — сохранено; 409 — кто-то успел поменять файл раньше нас (надо повторить).
    players.sort(key=lambda p: p["nick"].lower())
    text = json.dumps({"players": players}, indent=2, ensure_ascii=False)
    body = {"message": message,
            "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
            "sha": sha}
    response = requests.put(FILE_URL, headers=github_headers(), json=body, timeout=30)
    return response.status_code


def find_player(players, nick):
    # Ник ищем без учёта больших/маленьких букв: HE_MAKC и he_makc — один игрок
    for player in players:
        if player["nick"].lower() == nick.lower():
            return player
    return None


def check_login(nickname, password):
    # Возвращаем запись игрока, если ник и пароль верные, иначе None
    players, sha = load_players()
    player = find_player(players, nickname)
    if player is None:
        return None
    if hmac.compare_digest(hash_password(password, player["salt"]), player["hash"]):
        return player
    return None


def change_players(change, message):
    # Общая часть для регистрации, смены пароля и удаления:
    # скачать список -> изменить (функция change) -> сохранить.
    # Если двое меняют список одновременно, GitHub ответит 409 — тогда пробуем ещё раз.
    for attempt in range(3):
        players, sha = load_players()
        change(players)
        status = save_players(players, sha, message)
        if status in (200, 201):
            return
        if status not in (409, 422):
            raise RuntimeError("GitHub не принял изменения (код " + str(status) + ")")
    raise RuntimeError("Не получилось сохранить, попробуй ещё раз")


def register(nickname, password):
    # Новый аккаунт. Если ник уже занят — ошибка (чужой аккаунт так не перезаписать).
    def add(players):
        if find_player(players, nickname):
            raise RuntimeError("Ник " + nickname + " уже занят")
        salt = secrets.token_hex(16)
        players.append({"nick": nickname, "salt": salt, "hash": hash_password(password, salt)})
    change_players(add, "Регистрация " + nickname)


def set_password(nickname, password):
    # Для админа: задать новый пароль (или создать аккаунт, если его нет)
    def change(players):
        salt = secrets.token_hex(16)
        player = find_player(players, nickname)
        if player:
            player["salt"] = salt
            player["hash"] = hash_password(password, salt)
        else:
            players.append({"nick": nickname, "salt": salt, "hash": hash_password(password, salt)})
    change_players(change, "Новый пароль " + nickname)


def remove_player(nickname):
    # Для админа: удалить аккаунт
    def remove(players):
        player = find_player(players, nickname)
        if player is None:
            raise RuntimeError("Игрока " + nickname + " нет")
        players.remove(player)
    change_players(remove, "Удалён " + nickname)
