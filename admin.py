# admin.py — для владельца: сменить пароль игроку, удалить игрока, показать список.
# Изменения сразу сохраняются на GitHub — git push не нужен.

import re
import getpass    # ввод пароля без показа на экране
import accounts


def ask_nick():
    nick = input("Ник: ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", nick):
        print("Ник должен быть 3–16 символов: латинские буквы, цифры и _")
        return None
    return nick


print("1 — задать новый пароль (или создать аккаунт)")
print("2 — удалить игрока")
print("3 — показать список игроков")
choice = input("Выбор: ").strip()

try:
    if choice == "1":
        nick = ask_nick()
        if nick:
            password = getpass.getpass("Новый пароль (символы не видны): ")
            if len(password) < 4:
                print("Пароль слишком короткий — нужно хотя бы 4 символа")
            elif getpass.getpass("Повтори пароль: ") != password:
                print("Пароли не совпали — ничего не изменено")
            else:
                accounts.set_password(nick, password)
                print("Готово: пароль для", nick, "сохранён на GitHub")
    elif choice == "2":
        nick = ask_nick()
        if nick:
            accounts.remove_player(nick)
            print("Готово: игрок", nick, "удалён")
    elif choice == "3":
        players, sha = accounts.load_players()
        for p in players:
            print(" -", p["nick"])
        print("Всего:", len(players))
    else:
        print("Нет такого пункта")
except Exception as error:
    print("Ошибка:", error)
