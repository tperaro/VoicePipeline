import threading
import time
import unittest

from studio.events import Event, EventBus, JobRunner, error_message


def wait_events(bus: EventBus, n: int, timeout: float = 5.0) -> list[Event]:
    got: list[Event] = []
    end = time.monotonic() + timeout
    while len(got) < n and time.monotonic() < end:
        got.extend(bus.drain())
        time.sleep(0.005)
    return got


class PtError(Exception):
    """Excecao do projeto: str(e) ja e a mensagem em PT."""


class EventBusTest(unittest.TestCase):
    def test_post_and_drain_in_order(self):
        bus = EventBus()
        bus.post("a", x=1)
        bus.post("b")
        evs = bus.drain()
        self.assertEqual([Event("a", {"x": 1}), Event("b", {})], evs)
        self.assertEqual([], bus.drain())

    def test_post_from_many_threads(self):
        bus = EventBus()

        def producer(k):
            for i in range(200):
                bus.post("n", k=k, i=i)

        threads = [threading.Thread(target=producer, args=(k,)) for k in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        evs = bus.drain()
        self.assertEqual(800, len(evs))
        for k in range(4):
            self.assertEqual(list(range(200)), [e.data["i"] for e in evs if e.data["k"] == k])


class ErrorMessageTest(unittest.TestCase):
    def test_project_errors_keep_message(self):
        self.assertEqual("Câmera não encontrada", error_message(PtError("Câmera não encontrada")))

    def test_builtin_errors_get_type(self):
        self.assertEqual("ValueError: boom", error_message(ValueError("boom")))

    def test_message_attribute_wins(self):
        e = PtError("detalhe tecnico")
        e.message = "Mensagem em PT"
        self.assertEqual("Mensagem em PT", error_message(e))

    def test_empty_message_uses_type(self):
        self.assertEqual("PtError", error_message(PtError()))


class JobRunnerTest(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.runner = JobRunner(self.bus, "teste")
        self.addCleanup(self.runner.close)

    def test_ok_event_with_result(self):
        self.runner.submit("soma", lambda a, b=0: a + b, 2, b=3)
        evs = wait_events(self.bus, 1)
        self.assertEqual([Event("job_ok", {"job": "soma", "runner": "teste", "result": 5})], evs)

    def test_fail_event_with_message_and_traceback(self):
        def bad():
            raise ValueError("boom")

        self.runner.submit("ruim", bad)
        (ev,) = wait_events(self.bus, 1)
        self.assertEqual("job_fail", ev.kind)
        self.assertEqual("ruim", ev.data["job"])
        self.assertEqual("ValueError: boom", ev.data["message"])
        self.assertIn("Traceback", ev.data["traceback"])
        self.assertIn("in bad", ev.data["traceback"])

    def test_runner_survives_failures_even_base_exceptions(self):
        def exits():
            raise SystemExit(3)

        self.runner.submit("sai", exits)
        self.runner.submit("depois", lambda: "ok")
        evs = wait_events(self.bus, 2)
        self.assertEqual(["job_fail", "job_ok"], [e.kind for e in evs])
        self.assertEqual("ok", evs[1].data["result"])

    def test_jobs_run_serially_in_order(self):
        running, peak, order = [0], [0], []
        lock = threading.Lock()

        def job(i):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.02)
            order.append(i)
            with lock:
                running[0] -= 1
            return i

        for i in range(5):
            self.runner.submit(f"j{i}", job, i)
        evs = wait_events(self.bus, 5)
        self.assertEqual(list(range(5)), order)
        self.assertEqual(1, peak[0])
        self.assertEqual([f"j{i}" for i in range(5)], [e.data["job"] for e in evs])

    def test_runs_in_worker_thread(self):
        self.runner.submit("thread", lambda: threading.current_thread().name)
        (ev,) = wait_events(self.bus, 1)
        self.assertNotEqual(threading.current_thread().name, ev.data["result"])
        self.assertIn("teste", ev.data["result"])

    def test_busy_and_current(self):
        gate = threading.Event()
        started = threading.Event()

        def waits():
            started.set()
            gate.wait(5)

        self.assertFalse(self.runner.busy)
        self.assertIsNone(self.runner.current)
        self.runner.submit("espera", waits)
        self.assertTrue(self.runner.busy)
        self.assertTrue(started.wait(5))
        self.assertEqual("espera", self.runner.current)
        gate.set()
        (ev,) = wait_events(self.bus, 1)
        # o evento so chega depois que o runner ja nao esta ocupado
        self.assertEqual("job_ok", ev.kind)
        self.assertFalse(self.runner.busy)
        self.assertIsNone(self.runner.current)

    def test_busy_is_false_when_last_event_arrives(self):
        for i in range(20):
            self.runner.submit(f"j{i}", time.sleep, 0.001)
        seen = []
        end = time.monotonic() + 5
        while len(seen) < 20 and time.monotonic() < end:
            for ev in self.bus.drain():
                seen.append((ev.data["job"], self.runner.busy))
        self.assertEqual(("j19", False), seen[-1])

    def test_close_cancels_queued_jobs_and_stops(self):
        gate = threading.Event()
        started = threading.Event()
        ran = []

        def first():
            started.set()
            gate.wait(5)
            return "primeiro"

        self.runner.submit("primeiro", first)
        self.runner.submit("segundo", ran.append, "segundo")
        self.assertTrue(started.wait(5))
        self.runner.close()
        with self.assertRaises(RuntimeError):
            self.runner.submit("depois", ran.append, "depois")
        gate.set()
        evs = wait_events(self.bus, 2)
        self.assertEqual(["job_ok", "job_fail"], [e.kind for e in evs])
        self.assertEqual("segundo", evs[1].data["job"])
        self.assertIn("fechando", evs[1].data["message"])
        self.assertEqual([], ran)
        self.runner._thread.join(5)
        self.assertFalse(self.runner._thread.is_alive())
        self.assertFalse(self.runner.busy)

    def test_close_is_idempotent(self):
        self.runner.close()
        self.runner.close()
        self.runner._thread.join(5)
        self.assertFalse(self.runner._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
