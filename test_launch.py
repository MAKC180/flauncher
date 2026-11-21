# test_launch.py — шаг 2: проверяем, что можем скачать и запустить Minecraft + NeoForge по нику.

import os            # работа с путями и папками
import uuid          # создание уникального ID игрока
import hashlib       # MD5 — нужен, чтобы ID игрока был всегда одинаковым для одного ника
import subprocess    # запуск другой программы (самой игры)
import minecraft_launcher_lib as mll  # библиотека, которая всё делает за нас
import pack_sync     # наш файл pack_sync.py — синхронизация сборки с GitHub

# ---------- Настройки ----------
NICKNAME = "HE_MAKC"           # ник игрока
RAM = "6G"                     # сколько памяти отдать игре

# Папка игры: C:\Users\<имя>\AppData\Roaming\.flauncher
GAME_DIR = os.path.join(os.environ["APPDATA"], ".flauncher")


def show_status(text):
    # Библиотека будет вызывать эту функцию и сообщать, что она сейчас делает
    print("  ...", text)


def offline_uuid(name):
    # Minecraft в офлайн-режиме делает ID игрока так же: MD5 от "OfflinePlayer:<ник>".
    # Поэтому у одного ника всегда один и тот же ID — и инвентарь в мире не потеряется.
    digest = hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest, version=3))


# 0) Скачиваем список сборки с GitHub — из него узнаём версии
print("0) Проверяю сборку на GitHub")
modpack = pack_sync.get_modpack()
MC_VERSION = modpack["minecraft"]
NEOFORGE_VERSION = modpack["neoforge"]

# 1) Ставим обычный Minecraft 1.21.1 (+ нужную ему Java 21 скачает сама библиотека)
print("1) Ставлю Minecraft", MC_VERSION)
mll.install.install_minecraft_version(MC_VERSION, GAME_DIR, callback={"setStatus": show_status})

# Узнаём, где лежит скачанная Java, чтобы ей же поставить NeoForge
runtime = mll.runtime.get_version_runtime_information(MC_VERSION, GAME_DIR)
java_path = mll.runtime.get_executable_path(runtime["name"], GAME_DIR)
print("   Java:", java_path)

# 2) Ставим NeoForge поверх
print("2) Ставлю NeoForge", NEOFORGE_VERSION)
neoforge = mll.mod_loader.get_mod_loader("neoforge")
neoforge.install(MC_VERSION, GAME_DIR, loader_version=NEOFORGE_VERSION,
                 callback={"setStatus": show_status}, java=java_path)

# Имя установленной версии, например "neoforge-21.1.265"
version_id = neoforge.get_installed_version(MC_VERSION, NEOFORGE_VERSION)
print("   Установлено:", version_id)

# 2.5) Приводим моды и настройки в соответствие со сборкой
print("2.5) Синхронизирую моды")
pack_sync.sync_pack(GAME_DIR, modpack, {"setStatus": show_status})

# 3) Собираем команду запуска игры
options = {
    "username": NICKNAME,
    "uuid": offline_uuid(NICKNAME),
    "token": "0",                       # «пустой» токен — вход без аккаунта Microsoft
    "jvmArguments": ["-Xmx" + RAM],     # лимит памяти для игры
    "launcherName": "Flauncher",
    "launcherVersion": "0.1",
}
command = mll.command.get_minecraft_command(version_id, GAME_DIR, options)

# 4) Запускаем игру и ждём, пока её закроют
print("3) Запускаю игру как", NICKNAME)
subprocess.run(command, cwd=GAME_DIR)
print("Игра закрыта.")
