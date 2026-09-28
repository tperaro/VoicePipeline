"""Lado do app do conversor RVC: inicia o worker e troca JSON por linha com ele (so stdlib)."""

import json
import os
import queue
import subprocess
import threading
import time

from studio import procs
from studio.config import BASE_DIR, RVC_LOG, VENV_PYTHON

WORKER_MODULE = "studio.rvc_worker"
CLOSE_WAIT_S = 10.0


class RvcError(Exception):
    """str(e) e a mensagem para o usuario."""


def _read_lines(stream, lines: queue.Queue) -> None:
    # thread leitora: repassa as linhas do protocolo; None = EOF (worker morreu ou fechou)
    try:
        for line in stream:
            lines.put(line)
    except (OSError, ValueError):
        pass
    finally:
        lines.put(None)


class RvcClient:
    def __init__(self, python: str = VENV_PYTHON, log_path: str = RVC_LOG, env: dict | None = None):
        self.python = python
        self.log_path = log_path
        self.env = dict(env or {})
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue | None = None
        self._reader: threading.Thread | None = None
        self._lock = threading.Lock()
        self._seq = 0

    def start(self) -> None:
        # chamar na thread principal: o PDEATHSIG vale enquanto a thread que fez o spawn viver
        if self.alive():
            return
        self._reap()
        env = {**os.environ, **self.env}
        with open(self.log_path, "ab") as log:
            log.write(f"\n=== worker RVC iniciado {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n".encode())
            log.flush()
            p = procs.spawn([self.python, "-m", WORKER_MODULE], cwd=BASE_DIR, env=env,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
        lines: queue.Queue = queue.Queue()
        reader = threading.Thread(target=_read_lines, args=(p.stdout, lines), name="rvc-reader", daemon=True)
        reader.start()
        self._proc, self._lines, self._reader = p, lines, reader

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def request(self, op: str, timeout: float = 600, **params) -> dict:
        if not self._lock.acquire(timeout=timeout):
            raise RvcError("O conversor está ocupado com outro trabalho")
        try:
            return self._request(op, timeout, params)
        finally:
            self._lock.release()

    def load(self) -> dict:
        return self.request("load")

    def convert(self, inp: str, out: str, model_key: str) -> dict:
        # o worker faz chdir para o Applio: so caminhos absolutos
        return self.request("convert", input=os.path.abspath(inp), output=os.path.abspath(out), model=model_key)

    def close(self) -> None:
        p = self._proc
        if p is None:
            return
        try:
            p.stdin.close()                 # EOF: o worker termina o job atual e sai com 0
        except (OSError, ValueError):
            pass
        try:
            p.wait(timeout=CLOSE_WAIT_S)
        except subprocess.TimeoutExpired:
            p.terminate()
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        self._reap()

    def _request(self, op: str, timeout: float, params: dict) -> dict:
        p, lines = self._proc, self._lines
        if p is None or p.poll() is not None:
            raise RvcError("O conversor não está rodando")
        self._seq += 1
        rid = str(self._seq)
        try:
            p.stdin.write(json.dumps({"id": rid, "op": op, **params}) + "\n")
            p.stdin.flush()
        except (OSError, ValueError):
            raise RvcError(self._died_message(p)) from None
        deadline = time.monotonic() + timeout
        while True:
            try:
                line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                self._kill(p)
                raise RvcError(f"O conversor demorou demais ({op}) e foi encerrado") from None
            if line is None:
                raise RvcError(self._died_message(p))
            try:
                resp = json.loads(line)
            except ValueError:
                resp = None
            if not isinstance(resp, dict):
                self._kill(p)
                raise RvcError(f"Resposta inválida do conversor: {line.strip()[:200]}")
            if resp.get("id") != rid:
                continue                    # resposta velha de outro pedido
            if not resp.get("ok"):
                raise RvcError(resp.get("erro") or f"O conversor falhou ({op})")
            return resp

    def _died_message(self, p: subprocess.Popen) -> str:
        try:
            rc = p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._kill(p)
            rc = p.returncode
        return f"O conversor fechou inesperadamente (código {rc}) — veja {os.path.basename(self.log_path)}"

    @staticmethod
    def _kill(p: subprocess.Popen) -> None:
        try:
            p.kill()
            p.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass

    def _reap(self) -> None:
        p, reader = self._proc, self._reader
        if reader is not None:
            reader.join(timeout=2)
        if p is not None:
            for stream in (p.stdin, p.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        self._proc = self._lines = self._reader = None
