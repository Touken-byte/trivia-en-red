import json


def enviar_msg(sock, tipo, **datos):
    """Envía un mensaje JSON terminado en salto de línea."""
    mensaje = {"tipo": tipo, **datos}
    linea = json.dumps(mensaje, ensure_ascii=False) + "\n"
    sock.sendall(linea.encode("utf-8"))


class LectorMensajes:
    """Acumula bytes y entrega mensajes completos (uno por línea)."""

    def __init__(self, sock):
        self.sock = sock
        self.buffer = b""

    def leer(self):
        """Devuelve un diccionario (mensaje) o None si se cerró la conexión."""
        while b"\n" not in self.buffer:
            datos = self.sock.recv(4096)
            if not datos:
                return None
            self.buffer += datos
        linea, self.buffer = self.buffer.split(b"\n", 1)
        try:
            return json.loads(linea.decode("utf-8"))
        except json.JSONDecodeError:
            return {"tipo": "error", "texto": "Mensaje mal formado"}
