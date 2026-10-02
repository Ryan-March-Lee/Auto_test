import unittest

from presentation.qt.pages import BasePage, PageProtocol


class _Signal:
    def __init__(self, *, connect_error=None, disconnect_errors=0):
        self.slots = []
        self.connect_error = connect_error
        self.disconnect_errors = disconnect_errors

    def connect(self, slot):
        if self.connect_error is not None:
            raise self.connect_error
        self.slots.append(slot)

    def disconnect(self, slot):
        if self.disconnect_errors:
            self.disconnect_errors -= 1
            raise RuntimeError("disconnect failed")
        self.slots.remove(slot)


class _Controller:
    def __init__(self, *, progress=None, result=None):
        self.progress = progress or _Signal()
        self.result = result or _Signal()


class _Page(BasePage):
    def __init__(self):
        super().__init__()
        self.root = object()
        self.events = []

    def build_ui(self):
        self.events.append("build")
        return self.root

    def _connect_controller_signals(self, controller):
        self._connect_signal(controller.progress, self._on_progress)

    def _on_progress(self, value):
        self.events.append(value)

    def on_activated(self):
        self.events.append("activated")

    def on_deactivated(self):
        self.events.append("deactivated")


class PageContractTests(unittest.TestCase):
    def test_page_protocol_is_implementable_without_qt(self):
        page = _Page()
        self.assertIsInstance(page, PageProtocol)
        self.assertIs(page.build_ui(), page.root)

    def test_binding_registers_and_rebinding_disconnects_old_signals(self):
        page = _Page()
        first = _Controller()
        second = _Controller()

        page.bind_controller(first)
        first.progress.slots[0](10)
        page.bind_controller(second)
        second.progress.slots[0](20)

        self.assertEqual(page.events, [10, 20])
        self.assertEqual(first.progress.slots, [])
        self.assertEqual(len(second.progress.slots), 1)

    def test_close_disconnects_signals_and_is_idempotent(self):
        page = _Page()
        controller = _Controller()
        page.bind_controller(controller)

        page.close()
        page.close()
        self.assertIsNone(page.controller)
        self.assertEqual(controller.progress.slots, [])
        self.assertEqual(page.events, ["deactivated"])
        with self.assertRaises(RuntimeError):
            page.bind_controller(controller)

    def test_binding_none_is_rejected(self):
        with self.assertRaises(TypeError):
            _Page().bind_controller(None)

    def test_binding_rolls_back_partial_connections(self):
        page = _Page()
        controller = _Controller(result=_Signal(connect_error=RuntimeError("connect failed")))

        # _Page currently connects one signal; use a subclass that connects a
        # second signal to exercise the transaction boundary.
        class _TwoSignalPage(_Page):
            def _connect_controller_signals(self, controller):
                super()._connect_controller_signals(controller)
                self._connect_signal(controller.result, self._on_progress)

        page = _TwoSignalPage()
        with self.assertRaisesRegex(RuntimeError, "connect failed"):
            page.bind_controller(controller)

        self.assertIsNone(page.controller)
        self.assertEqual(controller.progress.slots, [])
        self.assertEqual(controller.result.slots, [])

    def test_close_continues_after_deactivation_hook_failure(self):
        class _FailingPage(_Page):
            def on_deactivated(self):
                super().on_deactivated()
                raise RuntimeError("deactivate failed")

        page = _FailingPage()
        controller = _Controller()
        page.bind_controller(controller)

        with self.assertRaisesRegex(RuntimeError, "deactivate failed"):
            page.close()

        self.assertTrue(page._closed)
        self.assertIsNone(page.controller)
        self.assertEqual(controller.progress.slots, [])

    def test_close_retries_failed_disconnect_and_cleans_other_signals(self):
        progress = _Signal(disconnect_errors=1)
        result = _Signal()

        class _TwoSignalPage(_Page):
            def _connect_controller_signals(self, controller):
                self._connect_signal(controller.progress, self._on_progress)
                self._connect_signal(controller.result, self._on_progress)

        page = _TwoSignalPage()
        controller = _Controller(progress=progress, result=result)
        page.bind_controller(controller)

        with self.assertRaisesRegex(RuntimeError, "disconnect failed"):
            page.close()

        self.assertTrue(page._closed)
        self.assertIsNone(page.controller)
        self.assertEqual(progress.slots, [page._on_progress])
        self.assertEqual(result.slots, [])

        page.close()
        self.assertEqual(progress.slots, [])


if __name__ == "__main__":
    unittest.main()
