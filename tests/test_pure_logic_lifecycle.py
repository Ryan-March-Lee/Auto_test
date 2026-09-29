"""第一层离线测试：取消协议和测量会话状态机。"""

import unittest

from app.cancellation import (
    CancellationMode,
    CancellationToken,
    MeasurementCancelled,
)
from domain.models import MeasurementSession, MeasurementState


class CancellationTokenTests(unittest.TestCase):
    def test_token_starts_active_and_raises_only_after_request(self):
        token = CancellationToken()

        self.assertFalse(token.is_cancelled)
        self.assertIsNone(token.mode)
        token.raise_if_cancelled()

        token.request_stop(reason="用户停止")
        self.assertTrue(token.is_cancelled)
        self.assertEqual(token.mode, CancellationMode.NORMAL)
        self.assertEqual(token.reason, "用户停止")
        with self.assertRaises(MeasurementCancelled) as context:
            token.raise_if_cancelled()
        self.assertIs(context.exception.mode, CancellationMode.NORMAL)
        self.assertEqual(context.exception.reason, "用户停止")

    def test_emergency_request_cannot_be_downgraded(self):
        token = CancellationToken()
        token.request_emergency_stop(reason="过流保护")
        token.request_stop(reason="普通停止")

        self.assertEqual(token.mode, CancellationMode.EMERGENCY)
        self.assertEqual(token.reason, "过流保护")

    def test_normal_request_can_be_escalated_to_emergency(self):
        token = CancellationToken()
        token.request_stop(reason="用户停止")
        token.request_emergency_stop(reason="紧急停止")

        self.assertEqual(token.mode, CancellationMode.EMERGENCY)
        self.assertEqual(token.reason, "紧急停止")


class MeasurementSessionTests(unittest.TestCase):
    def test_normal_lifecycle_records_timestamps_and_clean_state(self):
        session = MeasurementSession("offline-run")
        for transition in (session.validate, session.connect, session.prepare,
                           session.power_on, session.start):
            transition()
        self.assertEqual(session.state, MeasurementState.MEASURING)
        self.assertIsNotNone(session.started_at)

        session.complete()
        self.assertEqual(session.state, MeasurementState.COMPLETED)
        self.assertIsNotNone(session.ended_at)
        session.clean()
        self.assertEqual(session.state, MeasurementState.CLEANED)
        self.assertEqual(session.to_dict()["state"], "cleaned")

    def test_stop_preserves_normal_and_emergency_reason(self):
        normal = MeasurementSession("normal")
        normal.validate()
        normal.connect()
        normal.prepare()
        normal.stop("达到安全边界")
        self.assertEqual(normal.state, MeasurementState.STOPPING)
        self.assertEqual(normal.stop_reason, "达到安全边界")

        emergency = MeasurementSession("emergency")
        emergency.validate()
        emergency.connect()
        emergency.prepare()
        emergency.stop("检测到过流", emergency=True)
        self.assertEqual(emergency.stop_reason, "emergency: 检测到过流")

    def test_invalid_transition_is_rejected_and_cleaned_is_terminal(self):
        session = MeasurementSession("offline-run")
        with self.assertRaisesRegex(ValueError, "不允许此操作"):
            session.start()

        session.clean()
        with self.assertRaisesRegex(ValueError, "不允许此操作"):
            session.clean()
        with self.assertRaisesRegex(ValueError, "已清理的会话不能标记失败"):
            session.fail("迟到的错误")


if __name__ == "__main__":
    unittest.main()
