import json, queue, socket, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from protocolo import enviar_msg, LectorMensajes

TRIVIA = ("127.0.0.1", 8080)
WEB_PORT = 8000
PERMITIDOS = {"crear", "unirse", "iniciar", "respuesta", "chat"}
sesiones = {}
lock = threading.Lock()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def responder(self, code, tipo="text/plain", data=b""):
        self.send_response(code)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            self.responder(200, "text/html; charset=utf-8", open("index.html", "rb").read())
        elif u.path == "/events":
            self.eventos(parse_qs(u.query).get("id", [""])[0])
        else:
            self.responder(404)

    def eventos(self, sid):
        try:
            sock = socket.create_connection(TRIVIA)
        except OSError:
            return self.responder(503, data=b"Servidor de trivia apagado")
        q = queue.Queue()
        with lock:
            sesiones[sid] = sock

        def lector():
            l = LectorMensajes(sock)
            try:
                while True:
                    m = l.leer()
                    q.put(m)
                    if m is None:
                        break
            except OSError:
                q.put(None)
        threading.Thread(target=lector, daemon=True).start()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            while True:
                try:
                    m = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                if m is None:
                    m = {"tipo": "cerrado"}
                self.wfile.write(("data: " + json.dumps(m, ensure_ascii=False) + "\n\n").encode())
                self.wfile.flush()
                if m["tipo"] == "cerrado":
                    break
        except OSError:
            pass
        finally:
            with lock:
                if sesiones.get(sid) is sock:
                    sesiones.pop(sid, None)
            sock.close()

    def do_POST(self):
        sid = parse_qs(urlparse(self.path).query).get("id", [""])[0]
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            with lock:
                sock = sesiones.get(sid)
            tipo = body.get("tipo")
            if sock and tipo == "salir":
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            elif sock and tipo in PERMITIDOS:
                enviar_msg(sock, body.pop("tipo"), **body)
        except (OSError, ValueError, TypeError):
            pass
        self.responder(204)


if __name__ == "__main__":
    ThreadingHTTPServer.daemon_threads = True
    print(f"Abre http://localhost:{WEB_PORT} en varias pestañas (Ctrl+C para salir)")
    ThreadingHTTPServer(("0.0.0.0", WEB_PORT), H).serve_forever()
