import codecs
import os
import select
import socket
import sys
import termios
import threading
import time
import tty
from collections import deque

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from protocolo import enviar_msg, LectorMensajes

HOST = "127.0.0.1"
PORT = 8080

console = Console()
salir = threading.Event()

# ---- Estado de la interfaz (lo tocan varios hilos) ----
lock_ui = threading.Lock()
ui = {
    "apodo": "-",
    "pregunta": None,
    "fin_pregunta": 0.0,
    "respondida": False,
    "resultado": "",          # feedback de mi respuesta
    "revelada": "",           # respuesta correcta al cerrar la ronda
    "marcador": [],
    "titulo_marcador": "Marcador",
    "estado": "Esperando en la sala...",
    "entrada": "",            # lo que se está tecleando
    "log": deque(maxlen=6),
}
historial = []                # copia completa del registro, para imprimir al salir

MEDALLAS = {1: "🥇", 2: "🥈", 3: "🥉"}


def log(texto, estilo="white"):
    with lock_ui:
        ui["log"].append(Text(texto, style=estilo))
        historial.append((texto, estilo))


# ---------------- Dibujo ----------------
def panel_cabecera():
    with lock_ui:
        t = Text()
        t.append(" TRIVIA EN RED ", style="bold white on magenta")
        t.append(f"   Jugador: {ui['apodo']}", style="bold cyan")
        t.append(f"   |   {ui['estado']}", style="yellow")
    return Panel(t, border_style="magenta")


def panel_pregunta():
    with lock_ui:
        p = ui["pregunta"]
        fin = ui["fin_pregunta"]
        respondida = ui["respondida"]
        resultado = ui["resultado"]
        revelada = ui["revelada"]
    if not p:
        return Panel(Text("Esperando que el anfitrión escriba /iniciar ...", justify="center"),
                     title="Pregunta", border_style="blue", padding=(2, 2))

    restante = max(0.0, fin - time.time())
    total = max(1, p.get("segundos", 20))
    fraccion = min(1.0, restante / total)
    color = "green" if fraccion > 0.5 else "yellow" if fraccion > 0.25 else "red"

    ancho = 36
    llenos = int(ancho * fraccion)
    barra = Text()
    barra.append("█" * llenos, style=color)
    barra.append("░" * (ancho - llenos), style="grey37")
    barra.append(f"  {restante:4.1f}s", style=f"bold {color}")

    lineas = [Text(p["pregunta"], style="bold"), Text("")]
    for letra, opcion in zip("ABCDEFGH", p["opciones"]):
        linea = Text()
        linea.append(f"  {letra}) ", style="bold yellow")
        linea.append(str(opcion))
        lineas.append(linea)
    lineas.append(Text(""))
    lineas.append(barra)
    if revelada:
        lineas.append(Text(revelada, style="bold green"))
    elif respondida:
        estilo = "bold green" if resultado.startswith("¡Correcto") else "italic cyan"
        lineas.append(Text(resultado or "Respuesta enviada.", style=estilo))

    return Panel(Group(*lineas), title=f"Pregunta {p['numero']}/{p['total']}",
                 border_style=color, padding=(0, 2))


def panel_marcador():
    with lock_ui:
        datos = list(ui["marcador"])
        titulo = ui["titulo_marcador"]
    tabla = Table(border_style="cyan", expand=True)
    tabla.add_column("#", justify="right", width=3)
    tabla.add_column("Jugador")
    tabla.add_column("Pts", justify="right")
    for pos, (apodo, pts) in enumerate(datos, start=1):
        tabla.add_row(MEDALLAS.get(pos, str(pos)), str(apodo), str(pts))
    if not datos:
        tabla.add_row("", "(sin datos)", "")
    return Panel(tabla, title=titulo, border_style="cyan")


def panel_log():
    with lock_ui:
        lineas = list(ui["log"])
    return Panel(Group(*lineas) if lineas else Text(""), title="Mensajes",
                 border_style="grey50", padding=(0, 1))


def panel_entrada():
    with lock_ui:
        texto = ui["entrada"]
    t = Text()
    t.append("> ", style="bold green")
    t.append(texto)
    t.append("█", style="blink")
    return Panel(t, title="Escribe aquí y presiona Enter", border_style="green", padding=(0, 1))


def armar_pantalla():
    layout = Layout()
    layout.split_column(
        Layout(name="cabecera", size=3),
        Layout(name="centro", ratio=1),
        Layout(name="log", size=8),
        Layout(name="entrada", size=3),
        Layout(name="ayuda", size=1),
    )
    layout["centro"].split_row(Layout(name="pregunta", ratio=3), Layout(name="marcador", ratio=2))
    layout["cabecera"].update(panel_cabecera())
    layout["pregunta"].update(panel_pregunta())
    layout["marcador"].update(panel_marcador())
    layout["log"].update(panel_log())
    layout["entrada"].update(panel_entrada())
    layout["ayuda"].update(Text(" /nick <nombre>  /lista  /iniciar  /r <A-D> (o solo A-D)  /salir   |   lo demás es chat",
                                style="dim"))
    return layout


# ---------------- Red ----------------
def recibir(sock):
    lector = LectorMensajes(sock)
    try:
        while not salir.is_set():
            msg = lector.leer()
            if msg is None:
                log("El servidor cerró la conexión.", "red")
                break
            tipo = msg.get("tipo")
            if tipo == "info":
                texto = msg.get("texto", "")
                log(texto, "cyan")
                with lock_ui:
                    if texto.startswith("Te uniste como "):
                        ui["apodo"] = texto[len("Te uniste como "):].rstrip(".")
                        ui["estado"] = "En la sala"
                    elif texto.startswith("¡Correcto") or texto.startswith("Respuesta registrada"):
                        ui["respondida"] = True
                        ui["resultado"] = texto
                    elif texto.startswith("Respuesta correcta:"):
                        ui["revelada"] = texto
            elif tipo == "chat":
                log(f"{msg.get('de', '?')}: {msg.get('texto', '')}", "green")
            elif tipo == "pregunta":
                with lock_ui:
                    ui["pregunta"] = msg
                    ui["fin_pregunta"] = time.time() + msg.get("segundos", 20)
                    ui["respondida"] = False
                    ui["resultado"] = ""
                    ui["revelada"] = ""
                    ui["estado"] = f"Pregunta {msg.get('numero')}/{msg.get('total')}"
            elif tipo == "marcador":
                with lock_ui:
                    ui["marcador"] = msg.get("puntajes", [])
                    ui["titulo_marcador"] = "Marcador"
            elif tipo == "fin":
                with lock_ui:
                    ui["marcador"] = msg.get("puntajes", [])
                    ui["titulo_marcador"] = "RESULTADO FINAL"
                    ui["pregunta"] = None
                    ui["estado"] = "Fin de la trivia"
            elif tipo == "error":
                log(f"Error: {msg.get('texto', '')}", "red")
            else:
                log(str(msg), "grey50")
    except OSError:
        pass
    finally:
        salir.set()


def procesar_linea(sock, linea):
    """Devuelve False si hay que salir."""
    if not linea:
        return True
    if linea == "/salir":
        return False
    if linea.startswith("/nick "):
        enviar_msg(sock, "unirse", nombre=linea[6:].strip())
    elif linea == "/lista":
        enviar_msg(sock, "listar")
    elif linea == "/iniciar":
        enviar_msg(sock, "iniciar")
    elif linea.startswith("/r ") or linea.upper() in ("A", "B", "C", "D"):
        letra = linea[3:].strip() if linea.startswith("/r ") else linea
        enviar_msg(sock, "respuesta", opcion=letra.upper())
    else:
        enviar_msg(sock, "chat", texto=linea)
    return True


def leer_teclado(sock):
    """Lee el teclado tecla por tecla (modo cbreak) y dibuja lo escrito en la pantalla."""
    fd = sys.stdin.fileno()
    viejo = termios.tcgetattr(fd)
    decodificador = codecs.getincrementaldecoder("utf-8")(errors="ignore")
    esc = 0   # 0 normal, 1 tras ESC, 2 dentro de una secuencia (flechas, etc.)
    try:
        tty.setcbreak(fd)
        while not salir.is_set():
            listo, _, _ = select.select([fd], [], [], 0.1)
            if not listo:
                continue
            datos = os.read(fd, 1024)
            if not datos:
                break
            for ch in decodificador.decode(datos):
                if esc == 1:
                    esc = 2 if ch in "[O" else 0
                    continue
                if esc == 2:
                    if "@" <= ch <= "~":
                        esc = 0
                    continue
                if ch == "\x1b":
                    esc = 1
                elif ch in ("\r", "\n"):
                    with lock_ui:
                        linea = ui["entrada"].strip()
                        ui["entrada"] = ""
                    if not procesar_linea(sock, linea):
                        return
                elif ch in ("\x7f", "\x08"):
                    with lock_ui:
                        ui["entrada"] = ui["entrada"][:-1]
                elif ch == "\x04":      # Ctrl+D
                    return
                elif ch.isprintable():
                    with lock_ui:
                        if len(ui["entrada"]) < 100:
                            ui["entrada"] += ch
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, viejo)


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else HOST
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((host, PORT))
    except ConnectionRefusedError:
        console.print(f"[red]No se pudo conectar a {host}:{PORT}. ¿Está encendido el servidor?[/red]")
        return

    threading.Thread(target=recibir, args=(sock,), name="Receptor", daemon=True).start()

    try:
        # Live refresca la pantalla en su propio hilo llamando a armar_pantalla
        with Live(get_renderable=armar_pantalla, console=console,
                  screen=True, refresh_per_second=10):
            leer_teclado(sock)
    finally:
        salir.set()
        try:
            sock.close()
        except OSError:
            pass
        console.print("[yellow]Desconectado.[/yellow] Registro de la sesión:\n")
        for texto, estilo in historial:
            console.print(Text(texto, style=estilo))


if __name__ == "__main__":
    main()
