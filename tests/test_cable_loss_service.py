import unittest

from measurement_services import CableLossService
from app.events import CheckpointEvent, RealtimeDataEvent


class _Instrument:
    def __init__(self):
        self.measured = 0

    def set_frequency(self, frequency):
        pass

    def set_power(self, power):
        pass

    def rf_output_on(self):
        pass

    def set_center_frequency(self, frequency):
        pass

    def set_span(self, span):
        pass

    def measure_power_with_average(self):
        self.measured += 1
        return -10.0

    def rf_output_off(self):
        pass

    def close_all(self, close_rf=False):
        return []


class _Events:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class CableLossServiceTests(unittest.TestCase):
    def test_path2_confirmation_is_not_published_again_when_continuing(self):
        events = _Events()
        service = CableLossService(
            {"test_frequencies": [1.0], "attenuator": {"type": "10dB"}},
            _Instrument(),
            event_sink=events,
            sleep_fn=lambda _: None,
        )

        waiting = service.run(path2_confirmed=False)
        self.assertEqual(waiting["status"], "waiting")
        checkpoints = [event.checkpoint for event in events.events if isinstance(event, CheckpointEvent)]
        self.assertEqual(checkpoints, ["path1", "path2"])

        result = service.run(path2_confirmed=True)
        self.assertIn("cable_losses", result)
        checkpoints = [event.checkpoint for event in events.events if isinstance(event, CheckpointEvent)]
        self.assertEqual(checkpoints, ["path1", "path2"])

        realtime = [event for event in events.events if isinstance(event, RealtimeDataEvent)]
        self.assertEqual([event.data["path"] for event in realtime], [1, 2])
        self.assertEqual(realtime[-1].data["cable_losses"]["cable1"], 0.0)


if __name__ == "__main__":
    unittest.main()
