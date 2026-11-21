# launcher.py — Flauncher: окно лаунчера.
# Окно (tkinter) + работа в фоне (поток), чтобы окно не «зависало» во время скачивания.

import os
import re            # проверка ника по шаблону
import json          # сохранение настроек в файл
import uuid
import queue         # «почтовый ящик» между фоновой работой и окном
import ctypes        # чтобы спросить у Windows, сколько памяти в компьютере
import hashlib
import threading     # запуск работы в фоне
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

import minecraft_launcher_lib as mll
import pack_sync
import updater

# ---------- Настройки ----------
LAUNCHER_VERSION = "0.4"
GAME_DIR = os.path.join(os.environ["APPDATA"], ".flauncher")
SETTINGS_FILE = os.path.join(GAME_DIR, "flauncher_settings.json")
MIN_RAM = 2           # меньше 2 ГБ сборка точно не запустится

# Цвета окна
BG = "#1e1f29"        # фон
FG = "#e6e6e6"        # текст
ACCENT = "#3fa34d"    # зелёная кнопка

# «Почтовый ящик»: фоновая работа кладёт сюда сообщения, окно их забирает.
# Так нужно, потому что менять окно tkinter можно только из главного потока.
messages = queue.Queue()


# ---------- Настройки игрока (ник и память) ----------

class MemoryStatus(ctypes.Structure):
    # «Анкета», которую заполняет Windows (функция GlobalMemoryStatusEx).
    # Поля должны идти именно в таком порядке — так их описывает Windows.
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),      # всего памяти — это нам и нужно
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def get_max_ram():
    # Сколько всего памяти в компьютере (в ГБ) — это максимум ползунка
    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    total_gb = round(status.ullTotalPhys / 1024 ** 3)
    return max(MIN_RAM, total_gb)


def load_settings():
    # Читаем сохранённые настройки. Если файла ещё нет — значения по умолчанию.
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except (OSError, ValueError):
        settings = {}
    # Раньше память хранилась текстом "6 ГБ", теперь числом 6 — понимаем оба варианта
    ram = settings.get("ram", 6)
    if isinstance(ram, str):
        ram = int(ram.split()[0])
    settings["ram"] = ram
    return settings


def save_settings(nickname, ram):
    os.makedirs(GAME_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump({"nickname": nickname, "ram": ram}, f, ensure_ascii=False)


def offline_uuid(name):
    # ID игрока из ника — как в офлайн-режиме Minecraft (объяснение в test_launch.py)
    digest = hashlib.md5(("OfflinePlayer:" + name).encode("utf-8")).digest()
    return str(uuid.UUID(bytes=digest, version=3))


# ---------- Фоновая работа ----------

def make_callback():
    # Эти функции не трогают окно напрямую — только кладут сообщения в ящик
    return {
        "setStatus": lambda text: messages.put(("status", text)),
        "setMax": lambda maximum: messages.put(("max", maximum)),
        "setProgress": lambda value: messages.put(("value", value)),
    }


def check_launcher_update():
    # Запускается один раз при старте лаунчера
    if not updater.is_exe():
        # Запущены как launcher.py (во время разработки) — обновлять нечего
        messages.put(("done",))
        return

    callback = make_callback()
    try:
        callback["setStatus"]("Проверяю обновления лаунчера...")
        update = updater.check_for_update(LAUNCHER_VERSION)
        if update:
            new_version, download_url = update
            callback["setStatus"]("Скачиваю новую версию лаунчера " + new_version + "...")
            updater.install_update(download_url, callback)
            messages.put(("restart",))   # новая версия запущена — закрываем эту
            return
        callback["setStatus"]("Готов к запуску")
    except Exception:
        # Обновление не удалось (нет интернета и т.п.) — играть всё равно можно
        callback["setStatus"]("Не удалось проверить обновления лаунчера")
    messages.put(("done",))


def install_and_run(nickname, ram_gb):
    callback = make_callback()
    status = callback["setStatus"]

    try:
        status("Проверяю сборку на GitHub...")
        modpack = pack_sync.get_modpack()
        mc_version = modpack["minecraft"]
        neoforge_version = modpack["neoforge"]

        status("Проверяю Minecraft " + mc_version + "...")
        mll.install.install_minecraft_version(mc_version, GAME_DIR, callback=callback)

        runtime = mll.runtime.get_version_runtime_information(mc_version, GAME_DIR)
        java_path = mll.runtime.get_executable_path(runtime["name"], GAME_DIR)

        # NeoForge ставим, только если этой версии ещё нет (установщик долгий)
        neoforge = mll.mod_loader.get_mod_loader("neoforge")
        version_id = neoforge.get_installed_version(mc_version, neoforge_version)
        installed = [v["id"] for v in mll.utils.get_installed_versions(GAME_DIR)]
        if version_id not in installed:
            status("Ставлю NeoForge " + neoforge_version + "...")
            neoforge.install(mc_version, GAME_DIR, loader_version=neoforge_version,
                             callback=callback, java=java_path)

        pack_sync.sync_pack(GAME_DIR, modpack, callback)

        options = {
            "username": nickname,
            "uuid": offline_uuid(nickname),
            "token": "0",
            "jvmArguments": ["-Xmx" + ram_gb + "G"],
            "launcherName": "Flauncher",
            "launcherVersion": LAUNCHER_VERSION,
        }
        command = mll.command.get_minecraft_command(version_id, GAME_DIR, options)

        status("Запускаю игру...")
        messages.put(("hide",))   # прячем окно, пока идёт игра
        # CREATE_NO_WINDOW — чтобы не выскакивало чёрное окно консоли Java
        subprocess.run(command, cwd=GAME_DIR, creationflags=subprocess.CREATE_NO_WINDOW)
        messages.put(("show",))   # игра закрыта — показываем окно снова
        status("Игра закрыта")
    except Exception as error:
        messages.put(("error", str(error)))

    messages.put(("done",))       # работа закончена — можно снова нажимать «Играть»


# ---------- Окно ----------

def on_play():
    # Вызывается при нажатии кнопки «Играть»
    nickname = nick_entry.get().strip()
    # Ник: 3–16 символов, латинские буквы, цифры и _ (так требует Minecraft)
    if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", nickname):
        messagebox.showwarning("Ник", "Ник должен быть 3–16 символов:\nлатинские буквы, цифры и _")
        return

    ram = int(ram_scale.get())             # значение ползунка, например 6
    save_settings(nickname, ram)

    play_button.config(state="disabled")   # защита от двойного нажатия
    ram_gb = str(ram)                      # 6 -> "6" (для строки -Xmx6G)
    # daemon=True — фоновая работа закончится вместе с окном
    threading.Thread(target=install_and_run, args=(nickname, ram_gb), daemon=True).start()


def check_messages():
    # Каждые 100 мс забираем все сообщения из ящика и обновляем окно
    while True:
        try:
            message = messages.get_nowait()
        except queue.Empty:
            break
        kind = message[0]
        if kind == "status":
            status_label.config(text=message[1])
        elif kind == "max":
            progress_bar.config(maximum=max(message[1], 1))
            progress_bar["value"] = 0
        elif kind == "value":
            progress_bar["value"] = message[1]
        elif kind == "hide":
            window.withdraw()
        elif kind == "show":
            window.deiconify()
        elif kind == "error":
            window.deiconify()
            status_label.config(text="Ошибка")
            messagebox.showerror("Ошибка", message[1])
        elif kind == "done":
            play_button.config(state="normal")
            progress_bar["value"] = 0
        elif kind == "restart":
            window.destroy()   # закрываем старую версию лаунчера
            return
    window.after(100, check_messages)   # проверить ящик снова через 100 мс


window = tk.Tk()
window.title("Flauncher " + LAUNCHER_VERSION)
window.geometry("420x320")
window.resizable(False, False)
window.configure(bg=BG)

settings = load_settings()

tk.Label(window, text="Flauncher", font=("Segoe UI", 22, "bold"), bg=BG, fg=FG).pack(pady=(18, 10))

# Строка «Ник»
row = tk.Frame(window, bg=BG)
row.pack(pady=5)
tk.Label(row, text="Ник:", width=8, anchor="w", font=("Segoe UI", 11), bg=BG, fg=FG).pack(side="left")
nick_entry = tk.Entry(row, width=22, font=("Segoe UI", 11))
nick_entry.insert(0, settings.get("nickname", ""))
nick_entry.pack(side="left")

# Строка «Память»: ползунок + подпись «6 ГБ»
max_ram = get_max_ram()
row = tk.Frame(window, bg=BG)
row.pack(pady=5)
tk.Label(row, text="Память:", width=8, anchor="w", font=("Segoe UI", 11), bg=BG, fg=FG).pack(side="left")
ram_value_label = tk.Label(row, width=6, font=("Segoe UI", 11, "bold"), bg=BG, fg=FG)


def on_ram_change(value):
    # Ползунок вызывает эту функцию при каждом движении — обновляем подпись
    ram = int(float(value))
    ram_value_label.config(text=str(ram) + " ГБ")
    # Если Windows остаётся меньше 2 ГБ — предупреждаем оранжевым цветом
    if max_ram - ram < 2:
        ram_value_label.config(fg="#f0a030")
        ram_hint_label.config(text="Windows почти не останется памяти — компьютер может тормозить")
    else:
        ram_value_label.config(fg=FG)
        ram_hint_label.config(text="")


ram_scale = tk.Scale(row, from_=MIN_RAM, to=max_ram, resolution=1, orient="horizontal",
                     length=170, showvalue=False, command=on_ram_change,
                     bg=BG, fg=FG, troughcolor="#3a3b47", highlightthickness=0,
                     activebackground=ACCENT, sliderrelief="flat")
# Сохранённое значение не может быть больше, чем есть памяти
ram_scale.set(min(settings["ram"], max_ram))
ram_scale.pack(side="left")
ram_value_label.pack(side="left", padx=(8, 0))
# Подсказка под ползунком (пустая, пока память не выставлена почти на максимум)
ram_hint_label = tk.Label(window, text="", font=("Segoe UI", 8), bg=BG, fg="#f0a030")
ram_hint_label.pack()
on_ram_change(ram_scale.get())

# Кнопка «Играть»
play_button = tk.Button(window, text="ИГРАТЬ", font=("Segoe UI", 14, "bold"), width=14,
                        bg=ACCENT, fg="white", activebackground="#358a41", relief="flat",
                        command=on_play)
play_button.pack(pady=15)

# Полоска загрузки и текст под ней
progress_bar = ttk.Progressbar(window, length=360, mode="determinate")
progress_bar.pack()
status_label = tk.Label(window, text="Готов к запуску", font=("Segoe UI", 9), bg=BG, fg="#a0a0a0")
status_label.pack(pady=5)

# Пока проверяем обновления лаунчера — кнопка выключена
play_button.config(state="disabled")
threading.Thread(target=check_launcher_update, daemon=True).start()
# Отдельно, чтобы не задерживать проверку обновлений — убираем Flauncher.exe.old
threading.Thread(target=updater.cleanup_old, daemon=True).start()

check_messages()     # начинаем проверять «почтовый ящик»
window.mainloop()    # показываем окно и ждём действий игрока
