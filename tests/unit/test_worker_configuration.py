from healthcurve.worker import dose_reminders_enabled


def test_dose_reminders_require_polling_and_an_allowed_chat() -> None:
    assert dose_reminders_enabled(polling_enabled=True, allowed_chat_id=123)
    assert not dose_reminders_enabled(polling_enabled=False, allowed_chat_id=123)
    assert not dose_reminders_enabled(polling_enabled=True, allowed_chat_id=None)
