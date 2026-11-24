# launcher.py — Flauncher: окно лаунчера.
# Окно (tkinter) + работа в фоне (поток), чтобы окно не «зависало» во время скачивания.

import os
import re            # проверка ника по шаблону
import json          # сохранение настроек в файл
import uuid
import queue         # «почтовый ящик» между фоновой работой и окном
import ctypes        # чтобы спросить у Windows, сколько памяти в компьютере
import hashlib
import time          # пауза между проверками сервера
import threading     # запуск работы в фоне
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

import minecraft_launcher_lib as mll
import pack_sync
import updater
import accounts      # вход и регистрация (репозиторий flauncher-players)
import server_status # включён ли сервер и кто на нём играет

# ---------- Настройки ----------
LAUNCHER_VERSION = "0.10"
GAME_DIR = os.path.join(os.environ["APPDATA"], ".flauncher")
SETTINGS_FILE = os.path.join(GAME_DIR, "flauncher_settings.json")
MIN_RAM = 2           # меньше 2 ГБ сборка точно не запустится
NICK_RULE = r"[A-Za-z0-9_]{3,16}"   # ник: 3–16 символов, латиница, цифры и _ (так требует Minecraft)

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
    settings.setdefault("password", "")
    settings.setdefault("remember", True)
    settings.setdefault("disabled_mods", [])   # названия выключенных модов (окно «Моды»)
    return settings


def write_settings():
    # Записываем все настройки (словарь settings) в файл
    os.makedirs(GAME_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False)


def save_settings(nickname, ram, password, remember):
    # Пароль сохраняем на компьютере игрока, только если стоит галочка «Запомнить»
    settings["nickname"] = nickname
    settings["ram"] = ram
    settings["password"] = password if remember else ""
    settings["remember"] = remember
    write_settings()


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


def watch_server():
    # Работает в фоне всё время, пока открыт лаунчер:
    # раз в 30 секунд узнаёт статус сервера и кладёт его в «почтовый ящик».
    address = None
    while True:
        if address is None:
            # Адрес сервера берём из сборки на GitHub (modpack.json -> "server")
            try:
                server = pack_sync.get_modpack().get("server")
            except Exception:
                server = None
            if server:
                address = server["address"]
        if address:
            messages.put(("server", server_status.get_status(address)))
        time.sleep(30)


def install_and_run(nickname, password, ram_gb):
    callback = make_callback()
    status = callback["setStatus"]

    try:
        # Сначала проверяем ник и пароль — до долгих скачиваний
        status("Проверяю ник и пароль...")
        player = accounts.check_login(nickname, password)
        if player is None:
            raise RuntimeError("Неверный ник или пароль")
        # Берём ник точно как в списке (HE_MAKC, а не he_makc) —
        # от больших/маленьких букв зависит ID игрока, а с ним и вещи в мире
        nickname = player["nick"]

        status("Проверяю моды...")
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

        # Моды, выключенные в окне «Моды», не скачиваем (а если уже скачаны — удаляем)
        disabled_files = pack_sync.get_disabled_files(modpack, settings["disabled_mods"])
        pack_sync.sync_pack(GAME_DIR, modpack, callback, disabled_files)

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


def register_in_background(nickname, password):
    # Регистрация на GitHub (в фоне, чтобы окно не зависло)
    try:
        accounts.register(nickname, password)
        messages.put(("registered", nickname, password))
    except Exception as error:
        messages.put(("register_error", str(error)))


# ---------- Окно регистрации ----------

# Здесь храним части окна регистрации, пока оно открыто
register_ui = {}


def open_register():
    # Окно «Регистрация» поверх основного
    if register_ui.get("dialog"):
        register_ui["dialog"].lift()   # уже открыто — просто показать
        return

    dialog = tk.Toplevel(window)
    dialog.title("Регистрация")
    dialog.geometry("360x240")
    dialog.resizable(False, False)
    dialog.configure(bg=BG)
    dialog.transient(window)           # окно «привязано» к основному

    tk.Label(dialog, text="Новый аккаунт", font=("Segoe UI", 14, "bold"), bg=BG, fg=FG).pack(pady=(14, 8))

    entries = {}
    for key, title, hidden in [("nick", "Ник:", False), ("pass1", "Пароль:", True), ("pass2", "Повтор:", True)]:
        row = tk.Frame(dialog, bg=BG)
        row.pack(pady=3)
        tk.Label(row, text=title, width=8, anchor="w", font=("Segoe UI", 11), bg=BG, fg=FG).pack(side="left")
        entry = tk.Entry(row, width=20, font=("Segoe UI", 11), show="•" if hidden else "")
        entry.pack(side="left")
        entries[key] = entry

    button = tk.Button(dialog, text="Создать аккаунт", font=("Segoe UI", 11, "bold"),
                       bg=ACCENT, fg="white", relief="flat", command=on_register)
    button.pack(pady=10)
    info = tk.Label(dialog, text="", font=("Segoe UI", 9), bg=BG, fg="#a0a0a0")
    info.pack()

    def on_close():
        register_ui.clear()
        dialog.destroy()

    dialog.protocol("WM_DELETE_WINDOW", on_close)   # нажали крестик
    register_ui.update(dialog=dialog, entries=entries, button=button, info=info, close=on_close)
    entries["nick"].focus()


def on_register():
    # Нажали «Создать аккаунт»
    entries = register_ui["entries"]
    nickname = entries["nick"].get().strip()
    password = entries["pass1"].get()
    if not re.fullmatch(NICK_RULE, nickname):
        messagebox.showwarning("Ник", "Ник должен быть 3–16 символов:\nлатинские буквы, цифры и _",
                               parent=register_ui["dialog"])
        return
    if len(password) < 4:
        messagebox.showwarning("Пароль", "Пароль должен быть хотя бы 4 символа", parent=register_ui["dialog"])
        return
    if entries["pass2"].get() != password:
        messagebox.showwarning("Пароль", "Пароли не совпадают", parent=register_ui["dialog"])
        return

    register_ui["button"].config(state="disabled")
    register_ui["info"].config(text="Создаю аккаунт...")
    threading.Thread(target=register_in_background, args=(nickname, password), daemon=True).start()


# ---------- Окно «Моды» ----------

def open_mods():
    # Окно с галочками: какие необязательные моды включены
    try:
        modpack = pack_sync.get_modpack()   # список берём из сборки на GitHub
    except Exception as error:
        messagebox.showerror("Моды", "Не удалось получить список модов:\n" + str(error))
        return
    optional = modpack.get("optional", [])
    if not optional:
        messagebox.showinfo("Моды", "В сборке нет модов, которые можно выключить")
        return

    dialog = tk.Toplevel(window)
    dialog.title("Моды")
    dialog.resizable(False, False)
    dialog.configure(bg=BG)
    dialog.transient(window)
    dialog.grab_set()   # пока окно открыто, основное окно не нажимается

    tk.Label(dialog, text="Необязательные моды", font=("Segoe UI", 14, "bold"),
             bg=BG, fg=FG).pack(pady=(12, 2), padx=20)
    tk.Label(dialog, text="Сними галочку, чтобы выключить мод.\nНа сервер и других игроков это не влияет.",
             font=("Segoe UI", 9), bg=BG, fg="#a0a0a0").pack(pady=(0, 8))

    # Для каждого мода — галочка. Галочка стоит, если мода нет в списке выключенных.
    checks = {}
    for entry in optional:
        name = entry["name"]
        var = tk.BooleanVar(value=name not in settings["disabled_mods"])
        tk.Checkbutton(dialog, text=name, variable=var, anchor="w",
                       font=("Segoe UI", 10), bg=BG, fg=FG, selectcolor=BG,
                       activebackground=BG, activeforeground=FG).pack(fill="x", padx=20)
        checks[name] = var

    def on_save():
        # Запоминаем названия модов, у которых галочка снята
        settings["disabled_mods"] = [name for name, var in checks.items() if not var.get()]
        write_settings()
        dialog.destroy()
        status_label.config(text="Моды сохранены — применятся при нажатии «ИГРАТЬ»")

    tk.Button(dialog, text="Сохранить", font=("Segoe UI", 11, "bold"), width=12,
              bg=ACCENT, fg="white", relief="flat", command=on_save).pack(pady=12)


# ---------- Основное окно ----------

def on_play():
    # Вызывается при нажатии кнопки «Играть»
    nickname = nick_entry.get().strip()
    if not re.fullmatch(NICK_RULE, nickname):
        messagebox.showwarning("Ник", "Ник должен быть 3–16 символов:\nлатинские буквы, цифры и _")
        return

    password = pass_entry.get()
    if not password:
        messagebox.showwarning("Пароль", "Введи пароль")
        return

    ram = int(ram_scale.get())             # значение ползунка, например 6
    save_settings(nickname, ram, password, remember_var.get())

    play_button.config(state="disabled")   # защита от двойного нажатия
    ram_gb = str(ram)                      # 6 -> "6" (для строки -Xmx6G)
    # daemon=True — фоновая работа закончится вместе с окном
    threading.Thread(target=install_and_run, args=(nickname, password, ram_gb), daemon=True).start()


def show_server_status(result):
    # Показываем статус сервера под ползунком памяти
    state = result["state"]
    if state == "online":
        text = "● Сервер онлайн · {} из {} · {} мс".format(result["online"], result["max"], result["ping"])
        server_label.config(text=text, fg="#4cc35e")            # зелёный
        players_label.config(text=", ".join(result["players"]))  # ники через запятую
    elif state == "no_radmin":
        server_label.config(text="● Radmin VPN выключен", fg="#f0a030")   # оранжевый
        players_label.config(text="Включи Radmin VPN")
    else:
        server_label.config(text="● Сервер выключен", fg="#a0a0a0")   # серый
        players_label.config(text="")


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
        elif kind == "registered":
            # Аккаунт создан: закрываем окно регистрации и подставляем ник и пароль
            nickname, password = message[1], message[2]
            if register_ui:
                register_ui["close"]()
            nick_entry.delete(0, "end")
            nick_entry.insert(0, nickname)
            pass_entry.delete(0, "end")
            pass_entry.insert(0, password)
            messagebox.showinfo("Регистрация", "Аккаунт " + nickname + " создан!\nТеперь нажми «ИГРАТЬ».")
        elif kind == "server":
            show_server_status(message[1])
        elif kind == "register_error":
            if register_ui:
                register_ui["button"].config(state="normal")
                register_ui["info"].config(text="")
                messagebox.showerror("Регистрация", message[1], parent=register_ui["dialog"])
    window.after(100, check_messages)   # проверить ящик снова через 100 мс


window = tk.Tk()
window.title("Flauncher " + LAUNCHER_VERSION)
window.geometry("420x470")
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

# Строка «Пароль» — show="•" прячет символы
row = tk.Frame(window, bg=BG)
row.pack(pady=5)
tk.Label(row, text="Пароль:", width=8, anchor="w", font=("Segoe UI", 11), bg=BG, fg=FG).pack(side="left")
pass_entry = tk.Entry(row, width=22, font=("Segoe UI", 11), show="•")
pass_entry.insert(0, settings["password"])
pass_entry.pack(side="left")

# Галочка «Запомнить пароль»
remember_var = tk.BooleanVar(value=settings["remember"])
tk.Checkbutton(window, text="Запомнить пароль", variable=remember_var,
               font=("Segoe UI", 9), bg=BG, fg=FG, selectcolor=BG,
               activebackground=BG, activeforeground=FG).pack()

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

# Статус сервера: строка «● Сервер онлайн · ...» и ники игроков под ней
server_label = tk.Label(window, text="● Проверяю сервер...", font=("Segoe UI", 10), bg=BG, fg="#a0a0a0")
server_label.pack(pady=(6, 0))
players_label = tk.Label(window, text="", font=("Segoe UI", 9), bg=BG, fg="#a0a0a0", wraplength=380)
players_label.pack()

# Кнопка «Играть»
play_button = tk.Button(window, text="ИГРАТЬ", font=("Segoe UI", 14, "bold"), width=14,
                        bg=ACCENT, fg="white", activebackground="#358a41", relief="flat",
                        command=on_play)
play_button.pack(pady=(8, 4))

# Кнопка «Регистрация» — неброская, под «Играть»
tk.Button(window, text="Нет аккаунта? Регистрация", font=("Segoe UI", 9, "underline"),
          bg=BG, fg="#8ab4f8", activebackground=BG, activeforeground="#aecbfa",
          relief="flat", bd=0, cursor="hand2", command=open_register).pack(pady=(0, 10))


# Кнопка «Настройки» (шестерёнка) в правом верхнем углу.
# При нажатии раскрывается меню: «Моды» и «Расположение папки».
def open_game_folder():
    # Открываем папку лаунчера (%APPDATA%\.flauncher) в Проводнике
    os.makedirs(GAME_DIR, exist_ok=True)
    os.startfile(GAME_DIR)


settings_menu = tk.Menu(window, tearoff=0, font=("Segoe UI", 10))   # tearoff=0 — без лишней пунктирной строки
settings_menu.add_command(label="Моды", command=open_mods)
settings_menu.add_command(label="Расположение папки", command=open_game_folder)


def show_settings_menu():
    # Показываем меню прямо под кнопкой-шестерёнкой
    x = settings_button.winfo_rootx()
    y = settings_button.winfo_rooty() + settings_button.winfo_height()
    settings_menu.tk_popup(x, y)


settings_button = tk.Button(window, text="⚙", font=("Segoe UI Symbol", 14),
                            bg=BG, fg=FG, activebackground="#3a3b47", activeforeground=FG,
                            relief="flat", bd=0, cursor="hand2", command=show_settings_menu)
# place — поставить в точное место: в правый верхний угол с отступом 8 точек
settings_button.place(relx=1.0, x=-8, y=8, anchor="ne")

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
# Следим за сервером (раз в 30 секунд)
threading.Thread(target=watch_server, daemon=True).start()

check_messages()     # начинаем проверять «почтовый ящик»
window.mainloop()    # показываем окно и ждём действий игрока
