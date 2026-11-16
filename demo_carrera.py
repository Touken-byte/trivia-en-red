"""Bots para la demostración de la condición de carrera.
Uso: python3 demo_carrera.py [cantidad_de_bots]   (por defecto 3)
Bot1 crea una sala nueva y los demás se unen con su código."""
import json
import socket
import sys
import threading
import time

from protocolo import enviar_msg, LectorMensajes

HOST = "127.0.0.1"
PORT = 8080
N = int(sys.argv[1]) if len(sys.argv) > 1 else 3

with open("preguntas.json", encoding="utf-8") as f:
    CORRECTAS = {p["pregunta"]: p["correcta"] for p in json.load(f)}

barrera = threading.Barrier(N)       # para responder todos a la vez
unidos = threading.Barrier(N)        # para iniciar cuando todos entraron
sala_lista = threading.Event()
codigo_sala = {}
lock_print = threading.Lock()


def decir(texto):
    with lock_print:
        print(texto, flush=True)


def bot(i):
    nombre = f"Bot{i}"
    sock = socket.create_connection((HOST, PORT))
    lector = LectorMensajes(sock)

    if i == 1:
        enviar_msg(sock, "crear", nombre=nombre, avatar=0)
    else:
        sala_lista.wait(10)
        enviar_msg(sock, "unirse", nombre=nombre, codigo=codigo_sala.get("c", ""), avatar=i)

    while True:                       # esperar confirmación de la sala
        msg = lector.leer()
        if msg is None:
            return
        if msg["tipo"] == "sala":
            if i == 1:
                codigo_sala["c"] = msg["codigo"]
                decir(f"Sala creada: {msg['codigo']}")
                sala_lista.set()
            break
        if msg["tipo"] == "error":
            decir(f"[{nombre}] Error: {msg.get('texto')}")
            return

    unidos.wait(timeout=15)
    if i == 1:
        time.sleep(1)
        enviar_msg(sock, "iniciar")

    while True:
        msg = lector.leer()
        if msg is None:
            break
        tipo = msg.get("tipo")
        if tipo == "pregunta":
            letra = CORRECTAS.get(msg["pregunta"], "A")
            try:
                barrera.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
            enviar_msg(sock, "respuesta", opcion=letra)
        elif tipo == "resultado" and msg.get("estado") != "incorrecta":
            decir(f"[{nombre}] {msg.get('texto')}")
        elif tipo == "alerta":
            decir(f"[{nombre}] ⚠ {msg.get('texto')}")
        elif tipo == "fin":
            if i == 1:
                decir("\n--- RESULTADO FINAL ---")
                for apodo, pts in msg.get("puntajes", []):
                    decir(f"{apodo}: {pts}")
            break
    sock.close()


def main():
    hilos = []
    for i in range(1, N + 1):
        t = threading.Thread(target=bot, args=(i,), name=f"Bot{i}")
        t.start()
        hilos.append(t)
        time.sleep(0.3)
    for t in hilos:
        t.join()


if __name__ == "__main__":
    main()
