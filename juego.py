import json
import random
import threading
import time

ARCHIVO_PREGUNTAS = "preguntas.json"
ARCHIVO_TEMAS = "preguntas_temas.json"
PREGUNTAS_POR_PARTIDA = 5

PUNTOS_PRIMERO = 100
PUNTOS_SIGUIENTES = 50

# Interruptor para la demostración del Paso 9:
#   True  -> protege con Lock (correcto)
#   False -> sin protección (condición de carrera)
USAR_LOCK = True


def cargar_preguntas():
    """Lee el banco de preguntas y las devuelve en orden aleatorio."""
    with open(ARCHIVO_PREGUNTAS, encoding="utf-8") as f:
        preguntas = json.load(f)
    random.shuffle(preguntas)
    return preguntas


def elegir_tema_y_preguntas(tema_id=None):
    """Elige un tema (al azar si no se indica) y 5 preguntas mezcladas de ese tema.
    Devuelve (tema_id, nombre_tema, catalogo, preguntas).
    'catalogo' es la lista de todos los temas, para dibujar la ruleta en el navegador."""
    with open(ARCHIVO_TEMAS, encoding="utf-8") as f:
        temas = json.load(f)["temas"]
    if tema_id is None:
        tema_id = random.choice(list(temas))
    preguntas = random.sample(temas[tema_id]["preguntas"], PREGUNTAS_POR_PARTIDA)
    catalogo = [{"id": k, "nombre": v["nombre"]} for k, v in temas.items()]
    return tema_id, temas[tema_id]["nombre"], catalogo, preguntas


def para_cliente(pregunta, numero, total, segundos):
    """Datos que se envían a los jugadores (SIN la respuesta correcta)."""
    return {
        "numero": numero,
        "total": total,
        "pregunta": pregunta["pregunta"],
        "opciones": pregunta["opciones"],
        "segundos": segundos,
    }


class Ronda:
    """Estado compartido de la pregunta actual. Lo tocan muchos hilos a la vez."""

    def __init__(self):
        self.lock = threading.Lock()
        self.activa = False
        self.correcta = None
        self.esperados = set()      # apodos que deben responder esta ronda
        self.respondieron = set()
        self.hubo_primero = False
        self.puntajes = {}
        self.todos_respondieron = threading.Event()

    def abrir(self, correcta, esperados):
        with self.lock:
            self.activa = True
            self.correcta = correcta
            self.esperados = set(esperados)
            self.respondieron = set()
            self.hubo_primero = False
            self.todos_respondieron.clear()

    def cerrar(self):
        with self.lock:
            self.activa = False

    def esperar(self, segundos):
        """Bloquea hasta que todos respondan o se acabe el tiempo.
        Devuelve True si terminó porque todos respondieron."""
        return self.todos_respondieron.wait(timeout=segundos)

    def quitar_esperado(self, apodo):
        """Si un jugador se desconecta, ya no hace falta esperarlo."""
        with self.lock:
            self.esperados.discard(apodo)
            self._revisar_todos()

    def _revisar_todos(self):
        # Se llama con el lock tomado
        if self.activa and self.esperados and self.esperados.issubset(self.respondieron):
            self.todos_respondieron.set()
        elif self.activa and not self.esperados:
            self.todos_respondieron.set()

    def responder(self, apodo, letra):
        """Procesa una respuesta. Devuelve (estado, puntos)."""
        if USAR_LOCK:
            with self.lock:
                resultado = self._procesar(apodo, letra, pausa=False)
                self._revisar_todos()
                return resultado
        # SIN LOCK: la pausa amplía la ventana de la condición de carrera
        resultado = self._procesar(apodo, letra, pausa=True)
        with self.lock:
            self._revisar_todos()
        return resultado

    def _procesar(self, apodo, letra, pausa):
        if not self.activa:
            return "sin_ronda", 0
        if apodo in self.respondieron:
            return "repetida", 0
        self.respondieron.add(apodo)

        if letra != self.correcta:
            return "incorrecta", 0

        if not self.hubo_primero:
            if pausa:
                time.sleep(0.05)
            self.hubo_primero = True
            self.puntajes[apodo] = self.puntajes.get(apodo, 0) + PUNTOS_PRIMERO
            return "correcta_primero", PUNTOS_PRIMERO

        self.puntajes[apodo] = self.puntajes.get(apodo, 0) + PUNTOS_SIGUIENTES
        return "correcta", PUNTOS_SIGUIENTES

    def marcador(self):
        with self.lock:
            return sorted(self.puntajes.items(), key=lambda x: -x[1])

    def asegurar_jugador(self, apodo):
        with self.lock:
            self.puntajes.setdefault(apodo, 0)

    def reiniciar_puntajes(self):
        with self.lock:
            self.puntajes = {}

    def instantanea(self):
        """Copia del estado de la ronda, tomada de forma atómica (para el panel del servidor)."""
        with self.lock:
            return {
                "activa": self.activa,
                "esperados": set(self.esperados),
                "respondieron": set(self.respondieron),
                "puntajes": dict(self.puntajes),
            }
