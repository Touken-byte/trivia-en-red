import random
import socket
import sys
import threading
import time
from collections import deque

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from protocolo import enviar_msg, LectorMensajes
import juego

HOST = "0.0.0.0"
PORT = 8080
SEGUNDOS_POR_PREGUNTA = 20
SEGUNDOS_TEMA = 8                # duración de la animación de la ruleta en el navegador
MAX_JUGADORES = 8
NUM_AVATARES = 8
LETRAS_CODIGO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

console = Console()

# ---- Recursos compartidos ----
salas = {}                       # {codigo: Sala}
lock_salas = threading.Lock()

registro = deque(maxlen=300)
lock_registro = threading.Lock()


def log(texto, estilo="white"):
    t = Text()
    t.append(time.strftime("%H:%M:%S "), style="dim")
    t.append(f"[{threading.current_thread().name}] ", style="bold")
    t.append(texto, style=estilo)
    with lock_registro:
        registro.append(t)


class Cliente:
    """Una conexión TCP. Tiene su propio candado de envío porque varios hilos
    (el suyo y el de la partida) pueden escribirle al mismo socket."""

    def __init__(self, sock, addr, hilo):
        self.sock = sock
        self.addr = f"{addr[0]}:{addr[1]}"
        self.hilo = hilo
        self.lock_envio = threading.Lock()
        self.apodo = None
        self.avatar = 0
        self.sala = None

    def enviar(self, tipo, **datos):
        try:
            with self.lock_envio:
                enviar_msg(self.sock, tipo, **datos)
            return True
        except OSError:
            return False


class Sala:
    def __init__(self, codigo):
        self.codigo = codigo
        self.lock = threading.Lock()
        self.jugadores = {}            # {apodo: Cliente}
        self.anfitrion = None
        self.estado = "espera"         # espera | jugando | terminada
        self.cerrada = False
        self.ronda = juego.Ronda()
        self.numero = 0
        self.total = 0
        self.pregunta = None
        self.fin = 0.0
        self.creada = time.time()
        self.primeros = []             # para detectar la condición de carrera
        self.lock_primeros = threading.Lock()

    def clientes(self):
        with self.lock:
            return list(self.jugadores.values())

    def broadcast(self, tipo, excepto=None, **datos):
        for c in self.clientes():
            if c is not excepto:
                c.enviar(tipo, **datos)

    def lista(self):
        with self.lock:
            return [{"nombre": a, "avatar": c.avatar, "anfitrion": a == self.anfitrion}
                    for a, c in self.jugadores.items()]

    def enviar_lista(self):
        self.broadcast("jugadores", codigo=self.codigo, lista=self.lista(), estado=self.estado)


# ---------------- Gestión de salas ----------------
def crear_sala():
    with lock_salas:
        while True:
            codigo = "".join(random.choice(LETRAS_CODIGO) for _ in range(4))
            if codigo not in salas:
                break
        sala = Sala(codigo)
        salas[codigo] = sala
    log(f"Sala {codigo} creada", "bold blue")
    return sala


def entrar(c, sala, nombre, avatar):
    """Devuelve None si entró, o el motivo del error."""
    with sala.lock:
        if sala.cerrada:
            return "La sala ya no existe."
        if sala.estado == "jugando":
            return "La partida ya comenzó. Espera a que termine o crea tu propia sala."
        if len(sala.jugadores) >= MAX_JUGADORES:
            return f"Sala llena (máximo {MAX_JUGADORES} jugadores). Espera a la próxima partida o crea tu propia sala."
        if nombre in sala.jugadores:
            return f"El apodo '{nombre}' ya está en uso en esta sala."
        c.apodo, c.avatar, c.sala = nombre, avatar, sala
        sala.jugadores[nombre] = c
        if sala.anfitrion is None:
            sala.anfitrion = nombre
    return None


def salir_de_sala(c):
    sala = c.sala
    if sala is None:
        return
    nuevo = None
    with sala.lock:
        sala.jugadores.pop(c.apodo, None)
        vacia = not sala.jugadores
        if sala.anfitrion == c.apodo:
            sala.anfitrion = next(iter(sala.jugadores), None)
            nuevo = sala.anfitrion
        if vacia:
            sala.cerrada = True
    c.sala = None
    sala.ronda.quitar_esperado(c.apodo)
    if vacia:
        with lock_salas:
            salas.pop(sala.codigo, None)
        log(f"Sala {sala.codigo} cerrada (quedó vacía)", "yellow")
    else:
        sala.broadcast("info", texto=f"{c.apodo} salió de la sala.")
        if nuevo:
            sala.broadcast("info", texto=f"{nuevo} es ahora el anfitrión.")
        sala.enviar_lista()


# ---------------- Partida (un hilo por sala) ----------------
def correr_partida(sala):
    """Envoltorio: pase lo que pase, la sala no se queda trabada en 'jugando'."""
    try:
        _partida(sala)
    except Exception as e:
        log(f"[{sala.codigo}] Error en la partida: {type(e).__name__}: {e}", "bold red")
        try:
            sala.broadcast("error", texto="La partida terminó por un error del servidor.")
        except Exception:
            pass
    finally:
        with sala.lock:
            sala.estado = "terminada"
            sala.pregunta = None
        sala.ronda.cerrar()
        try:
            sala.enviar_lista()
        except Exception:
            pass


def _partida(sala):
    r = sala.ronda
    tema_id, tema_nombre, catalogo, preguntas = juego.elegir_tema_y_preguntas()
    total = len(preguntas)

    r.reiniciar_puntajes()
    for c in sala.clientes():
        r.asegurar_jugador(c.apodo)
    with sala.lock:
        sala.total, sala.numero = total, 0

    log(f"[{sala.codigo}] Partida iniciada: {len(sala.clientes())} jugadores, {total} preguntas",
        "bold magenta")
    sala.broadcast("inicio", total=total)
    time.sleep(0.2)

    # El servidor elige el tema: todos los jugadores de la sala ven el mismo resultado
    log(f"[{sala.codigo}] Tema elegido: {tema_nombre}", "bold cyan")
    sala.broadcast("tema", id=tema_id, nombre=tema_nombre,
                   catalogo=catalogo, segundos=SEGUNDOS_TEMA)
    time.sleep(SEGUNDOS_TEMA + 2)

    for i, p in enumerate(preguntas, start=1):
        with sala.lock_primeros:
            sala.primeros.clear()
        with sala.lock:
            apodos = list(sala.jugadores)
            if apodos:
                r.abrir(p["correcta"], apodos)
                sala.numero = i
                sala.pregunta = p["pregunta"]
                sala.fin = time.time() + SEGUNDOS_POR_PREGUNTA
        if not apodos:
            break
        log(f"[{sala.codigo}] Pregunta {i}/{total}: {p['pregunta']}", "bold yellow")
        sala.broadcast("pregunta", **juego.para_cliente(p, i, total, SEGUNDOS_POR_PREGUNTA))

        todos = r.esperar(SEGUNDOS_POR_PREGUNTA)
        r.cerrar()
        sala.broadcast("info", texto="¡Todos respondieron!" if todos else "¡Se acabó el tiempo!")
        time.sleep(0.3)

        with sala.lock_primeros:
            primeros = list(sala.primeros)
        if len(primeros) > 1:
            msg = f"CONDICIÓN DE CARRERA: {len(primeros)} jugadores fueron PRIMERO ({', '.join(primeros)})"
            log(f"[{sala.codigo}] {msg}", "bold white on red")
            sala.broadcast("alerta", texto=msg)

        idx = "ABCDEFGH".index(p["correcta"])
        sala.broadcast("revelar", letra=p["correcta"], texto=p["opciones"][idx])
        sala.broadcast("marcador", puntajes=r.marcador())
        time.sleep(3)

    sala.broadcast("fin", puntajes=r.marcador())
    with sala.lock:
        sala.estado = "terminada"
        sala.pregunta = None
    sala.enviar_lista()
    log(f"[{sala.codigo}] Partida terminada", "bold magenta")


# ---------------- Atención de cada cliente ----------------
def responder(c, msg):
    sala = c.sala
    letra = str(msg.get("opcion", "")).strip().upper()
    if letra not in ("A", "B", "C", "D"):
        c.enviar("error", texto="Opción inválida. Usa A, B, C o D.")
        return
    resultado, puntos = sala.ronda.responder(c.apodo, letra)
    quien = f"[{sala.codigo}] {c.apodo}"
    if resultado == "sin_ronda":
        c.enviar("error", texto="No hay pregunta activa.")
    elif resultado == "repetida":
        c.enviar("error", texto="Ya respondiste esta pregunta.")
    elif resultado == "incorrecta":
        c.enviar("resultado", estado="incorrecta", puntos=0, texto="Respuesta registrada.")
        log(f"{quien} respondió {letra} (incorrecta)", "red")
    elif resultado == "correcta_primero":
        with sala.lock_primeros:
            sala.primeros.append(c.apodo)
        c.enviar("resultado", estado="primero", puntos=puntos,
                 texto=f"¡Correcto y fuiste el PRIMERO! +{puntos}")
        log(f"{quien} respondió {letra} -> PRIMERO (+{puntos})", "bold green")
    else:
        c.enviar("resultado", estado="correcta", puntos=puntos, texto=f"¡Correcto! +{puntos}")
        log(f"{quien} respondió {letra} (+{puntos})", "green")


def procesar(c, msg):
    tipo = msg.get("tipo")

    if tipo in ("crear", "unirse"):
        if c.sala is not None:
            c.enviar("error", texto="Ya estás en una sala.")
            return
        nombre = str(msg.get("nombre", "")).strip()[:15]
        if not nombre:
            c.enviar("error", texto="El apodo no puede estar vacío.")
            return
        try:
            avatar = int(msg.get("avatar", 0)) % NUM_AVATARES
        except (TypeError, ValueError):
            avatar = 0

        if tipo == "crear":
            sala = crear_sala()
        else:
            codigo = str(msg.get("codigo", "")).strip().upper()
            with lock_salas:
                sala = salas.get(codigo)
            if sala is None:
                c.enviar("error", texto="Ese código de sala no existe.")
                return

        motivo = entrar(c, sala, nombre, avatar)
        if motivo:
            c.enviar("error", texto=motivo)
            return
        log(f"[{sala.codigo}] Se unió: {nombre}", "magenta")
        c.enviar("sala", codigo=sala.codigo, apodo=nombre)
        sala.enviar_lista()
        sala.broadcast("info", excepto=c, texto=f"{nombre} se unió a la sala.")
        return

    if c.sala is None:
        c.enviar("error", texto="Primero crea una sala o únete con un código.")
        return
    sala = c.sala

    if tipo == "chat":
        texto = str(msg.get("texto", ""))[:200]
        log(f"[{sala.codigo}] {c.apodo}: {texto}", "cyan")
        sala.broadcast("chat", de=c.apodo, texto=texto)
    elif tipo == "respuesta":
        responder(c, msg)
    elif tipo == "iniciar":
        with sala.lock:
            if sala.anfitrion != c.apodo:
                motivo = "Solo el anfitrión puede iniciar."
            elif sala.estado == "jugando":
                motivo = "La partida ya está en curso."
            else:
                motivo = None
                sala.estado = "jugando"
        if motivo:
            c.enviar("error", texto=motivo)
        else:
            sala.enviar_lista()
            threading.Thread(target=correr_partida, args=(sala,),
                             name=f"Partida-{sala.codigo}", daemon=True).start()
    else:
        c.enviar("error", texto=f"Tipo desconocido: {tipo}")


def manejar_cliente(clientsocket, address):
    c = Cliente(clientsocket, address, threading.current_thread().name)
    log(f"Conectado: {c.addr}", "green")
    lector = LectorMensajes(clientsocket)
    try:
        c.enviar("info", texto="Conectado al servidor de trivia.")
        while True:
            msg = lector.leer()
            if msg is None:
                break
            if not isinstance(msg, dict):
                c.enviar("error", texto="Mensaje inválido.")
                continue
            procesar(c, msg)
    except OSError:
        log("Conexión perdida", "red")
    except Exception as e:
        log(f"Error inesperado con {c.addr}: {type(e).__name__}: {e}", "bold red")
    finally:
        salir_de_sala(c)
        try:
            clientsocket.close()
        except OSError:
            pass
        log(f"Desconectado: {c.addr}" + (f" ({c.apodo})" if c.apodo else ""), "yellow")


# ---------------- Panel en vivo ----------------
def todas_salas():
    with lock_salas:
        return sorted(salas.values(), key=lambda s: s.creada)


def panel_cabecera():
    ss = todas_salas()
    njug = sum(len(s.jugadores) for s in ss)
    t = Text()
    t.append(" SERVIDOR TRIVIA ", style="bold white on blue")
    t.append(f" :{PORT} ", style="cyan")
    if juego.USAR_LOCK:
        t.append("CON LOCK ", style="bold green")
    else:
        t.append("SIN LOCK ", style="bold white on red")
    t.append(f"| Salas {len(ss)} | Jug. {njug} | Hilos {threading.active_count()}", style="yellow")
    return Panel(t, border_style="blue")


def panel_salas():
    tabla = Table(expand=True, border_style="cyan")
    for nombre, kw in [("Sala", {}), ("Estado", {}), ("Preg.", {"justify": "right"}),
                       ("Resp.", {"justify": "right"}), ("Jug.", {"justify": "right"}),
                       ("Anfitrión", {}), ("Tiempo", {"justify": "right"})]:
        tabla.add_column(nombre, no_wrap=True, overflow="ellipsis", **kw)
    for s in todas_salas():
        snap = s.ronda.instantanea()
        with s.lock:
            est, num, total, fin, anf, nj = (s.estado, s.numero, s.total, s.fin,
                                             s.anfitrion, len(s.jugadores))
        with s.lock_primeros:
            carrera = len(s.primeros) > 1
        color = {"espera": "yellow", "jugando": "bold green", "terminada": "magenta"}[est]
        preg = {"espera": "-", "jugando": f"{num}/{total}", "terminada": "fin"}[est]
        resp = f"{len(snap['respondieron'])}/{len(snap['esperados'])}" if snap["activa"] else "-"
        if carrera:
            resp += " ⚠"
        resto = f"{max(0, fin - time.time()):.0f}s" if snap["activa"] else "-"
        tabla.add_row(Text(s.codigo, style="bold cyan"), Text(est, style=color), preg, resp,
                      str(nj), str(anf), resto)
    if not todas_salas():
        tabla.add_row("(sin salas)", "", "", "", "", "", "")
    return Panel(tabla, title="Salas", border_style="cyan")


def panel_jugadores():
    tabla = Table(expand=True, border_style="green")
    for nombre, kw in [("Sala", {}), ("Jugador", {}), ("Hilo", {}), ("Estado", {}),
                       ("Pts", {"justify": "right"})]:
        tabla.add_column(nombre, no_wrap=True, overflow="ellipsis", **kw)
    hay = False
    for s in todas_salas():
        snap = s.ronda.instantanea()
        with s.lock:
            datos = [(a, c.hilo, a == s.anfitrion) for a, c in s.jugadores.items()]
        for apodo, hilo, es_anf in datos:
            hay = True
            if apodo in snap["respondieron"]:
                est = Text("✔ respondió", style="green")
            elif snap["activa"] and apodo in snap["esperados"]:
                est = Text("… pensando", style="yellow")
            else:
                est = Text("-", style="dim")
            tabla.add_row(s.codigo, apodo + (" ★" if es_anf else ""), hilo, est,
                          str(snap["puntajes"].get(apodo, 0)))
    if not hay:
        tabla.add_row("", "(nadie conectado)", "", "", "")
    return Panel(tabla, title="Jugadores (★ = anfitrión)", border_style="green")


def panel_log():
    with lock_registro:
        lineas = list(registro)[-8:]
    return Panel(Group(*lineas) if lineas else Text(""), title="Registro de eventos",
                 border_style="grey50", padding=(0, 1))


def armar_pantalla():
    layout = Layout()
    layout.split_column(
        Layout(name="cab", size=3),
        Layout(name="salas", ratio=1),
        Layout(name="jug", ratio=1),
        Layout(name="log", size=10),
    )
    layout["cab"].update(panel_cabecera())
    layout["salas"].update(panel_salas())
    layout["jug"].update(panel_jugadores())
    layout["log"].update(panel_log())
    return layout


def main():
    if "--sin-lock" in sys.argv:
        juego.USAR_LOCK = False

    serversocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    serversocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        serversocket.bind((HOST, PORT))
    except OSError as e:
        console.print(f"[red]No se pudo abrir el puerto {PORT}: {e}[/red]")
        return
    serversocket.listen(50)
    log(f"Servidor escuchando en el puerto {PORT} (Ctrl+C para detener)", "bold green")

    contador = 0
    try:
        with Live(get_renderable=armar_pantalla, console=console,
                  screen=True, refresh_per_second=8):
            while True:
                clientsocket, address = serversocket.accept()
                contador += 1
                threading.Thread(target=manejar_cliente, args=(clientsocket, address),
                                 name=f"Hilo-{contador}", daemon=True).start()
    except KeyboardInterrupt:
        pass
    finally:
        serversocket.close()

    console.print("[bold red]Servidor detenido.[/bold red] Registro completo:\n")
    with lock_registro:
        lineas = list(registro)
    for linea in lineas:
        console.print(linea)


if __name__ == "__main__":
    main()
