import sys
import socket
import threading
from rich.console import Console
from rich.panel import Panel

console = Console()
console.print(Panel.fit(
    "[bold green]Entorno listo[/bold green]\n"
    f"Python: {sys.version.split()[0]}\n"
    f"Hilo actual: {threading.current_thread().name}\n"
    f"Host: {socket.gethostname()}",
    title="Trivia en Red",
    border_style="cyan",
))
