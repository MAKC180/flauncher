# server_status.py — узнаём, включён ли сервер и кто на нём играет.
# Работает так же, как список серверов в самом Minecraft (Server List Ping):
# лаунчер спрашивает сервер «какой у тебя статус?», сервер отвечает, в игру никто не заходит.

import socket
from mcstatus import JavaServer   # готовая библиотека для такого запроса


def radmin_is_on():
    # Когда Radmin VPN включён, у компьютера появляется ещё один адрес — он начинается с "26.".
    # Спрашиваем у Windows все адреса этого компьютера и ищем среди них такой.
    try:
        my_addresses = [info[4][0] for info in
                        socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except OSError:
        return False
    for my_address in my_addresses:            # например ["26.181.188.165", "192.168.0.112"]
        if my_address.startswith("26."):
            return True
    return False


def get_status(address):
    # Возвращаем словарь, который лаунчер покажет под кнопкой «ИГРАТЬ»:
    #   state   — "online" (работает), "offline" (выключен) или "no_radmin" (выключен Radmin)
    #   online, max, ping, players — сколько игроков, сколько мест, пинг в мс, ники
    # Адреса Radmin VPN начинаются с "26." — для такого сервера сначала проверяем Radmin
    if address.startswith("26.") and not radmin_is_on():
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
