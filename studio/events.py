"""Fila de eventos das threads de trabalho para a thread do Tk e filas seriais de jobs (so stdlib)."""

import queue
import threading
import traceback
from dataclasses import dataclass

CANCELLED_MSG = "Cancelado: o app está fechando"


@dataclass
class Event:
    kind: str
    data: dict


class EventBus:
    def __init__(self):
        self._q: queue.SimpleQueue = queue.SimpleQueue()

    def post(self, kind: str, **data) -> None:
        # pode ser chamado de qualquer thread
        self._q.put(Event(kind, data))

    def drain(self) -> list[Event]:
        out = []
        while True:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                return out


def error_message(exc: BaseException) -> str:
    # excecoes do projeto ja trazem a mensagem em PT; as da stdlib ganham o tipo na frente
    msg = getattr(exc, "message", None) or str(exc)
    if not msg:
        return type(exc).__name__
    if type(exc).__module__ == "builtins":
        return f"{type(exc).__name__}: {msg}"
    return msg


class JobRunner:
    """Uma thread de trabalho longa com fila serial; todo job termina em job_ok ou job_fail."""

    def __init__(self, bus: EventBus, name: str):
        self.bus = bus
        self.name = name
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._pending = 0
        self._current: str | None = None
        self._closed = False
        self._thread = threading.Thread(target=self._loop, name=f"job-{name}", daemon=True)
        self._thread.start()

    def submit(self, job: str, fn, *args, **kwargs) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError(f"JobRunner {self.name} fechado")
            self._pending += 1
        self._q.put((job, fn, args, kwargs))

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._pending > 0

    @property
    def current(self) -> str | None:
        with self._lock:
            return self._current

    def close(self) -> None:
        # nao bloqueia: o job atual termina, os da fila viram job_fail e a thread sai
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._q.put(None)

    def _loop(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            job, fn, args, kwargs = item
            with self._lock:
                cancelled = self._closed
                if not cancelled:
                    self._current = job
            if cancelled:
                self._finish("job_fail", job=job, message=CANCELLED_MSG, traceback="")
            else:
                self._run(job, fn, args, kwargs)

    def _run(self, job: str, fn, args, kwargs) -> None:
        kind, data = "job_fail", {"message": "Falha desconhecida", "traceback": ""}
        try:
            data = {"result": fn(*args, **kwargs)}
            kind = "job_ok"
        except BaseException as e:
            data = {"message": error_message(e), "traceback": "".join(traceback.format_exception(e))}
        finally:
            self._finish(kind, job=job, **data)

    def _finish(self, kind: str, **data) -> None:
        # libera o "ocupado" antes de postar: quem recebe o ultimo evento ja ve busy == False
        with self._lock:
            self._pending -= 1
            self._current = None
        self.bus.post(kind, runner=self.name, **data)
