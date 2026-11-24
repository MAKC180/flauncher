# server_status.py — узнаём, включён ли сервер и кто на нём играет.
# Работает так же, как список серверов в самом Minecraft (Server List Ping):
# лаунчер спрашивает сервер «какой у тебя статус?», сервер отвечает, в игру никто не заходит.

import socket
from mcstatus import JavaServer   # готовая библиотека для такого запроса


def radmin_is_on(address):
    # Адреса Radmin VPN начинаются с "26.". Спрашиваем у Windows:
    # «через какой свой адрес ты пойдёшь к серверу?» (сами данные при этом не отправляются).
    # Если Radmin включён — Windows пойдёт через адрес Radmin, он тоже начинается с "26.".
    # Если Radmin выключен или не установлен — пойдёт через обычный интернет (192.168... и т.п.).
    host = address.split(":")[0]               # "26.1.82.220:25565" -> "26.1.82.220"
    try:
        test = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        test.connect((host, 25565))
        my_address = test.getsockname()[0]     # наш адрес, через который Windows идёт к серверу
        test.close()
    except OSError:
        return False                           # Windows вообще не знает дороги к серверу
    return my_address.startswith("26.")


def get_status(address):
    # Возвращаем словарь, который лаунчер покажет под кнопкой «ИГРАТЬ»:
    #   state   — "online" (работает), "offline" (выключен) или "no_radmin" (выключен Radmin)
    #   online, max, ping, players — сколько игроков, сколько мест, пинг в мс, ники
    if address.startswith("26.") and not radmin_is_on(address):
        return {"state": "no_radmin"}

    try:
        server = JavaServer.lookup(address, timeout=3)   # ждём ответа не больше 3 секунд
        status = server.status()
    except Exception:
        return {"state": "offline"}            # не ответил — значит выключен

    # sample — ники игроков онлайн (сервер присылает не больше 12)
    players = [player.name for player in (status.players.sample or [])]
    return {"state": "online",
            "online": status.players.online,
            "max": status.players.max,
            "ping": round(status.latency),
            "players": players}
