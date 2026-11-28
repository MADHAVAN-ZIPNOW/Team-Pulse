"""The admin's Claude choices survive a restart, and an account is always in use so nobody is asked to paste a token."""
import asyncio
import os

from app.services.claude_switcher import AccountSwitcher
from app.services.claude_usage import ClaudeUsage, LimitsCache

TOKENS = {"Abinaya": "tok-a", "Monika": "tok-m", "Dhamu": "tok-d"}


def make(tmp_path, default="", **kw):
    return ClaudeUsage(list(TOKENS), default, dict(TOKENS), state_file=str(tmp_path / "state.json"), **kw)


def test_an_account_is_in_use_from_the_start(tmp_path):
    assert make(tmp_path).active == "Abinaya"  # nobody had to choose
    assert make(tmp_path, default="Dhamu").active == "Dhamu"  # unless a default is configured


def test_the_admins_choice_and_settings_survive_a_restart(tmp_path):
    u = make(tmp_path)
    u.set_active("Monika")
    u.set_auto(False)
    u.set_model("Meetings", "opus")
    again = make(tmp_path)  # the server restarted
    assert again.active == "Monika" and again.auto_switch is False and again.models == {"Meetings": "opus"}
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "tok-m"  # and the app really runs on that token again


def test_there_is_never_no_account_so_nobody_is_asked_for_a_token(tmp_path):
    u = make(tmp_path)
    u.set_active("Dhamu")
    u.set_active(None)  # nothing can switch the app to "no account"
    assert u.active == "Dhamu"
    assert make(tmp_path).active == "Dhamu"  # and the admin's pick stays after a restart


def test_an_admins_pick_stays_until_an_admin_or_the_automatic_switch_changes_it(tmp_path):
    u = make(tmp_path)
    u.set_active("Monika")
    sw = AccountSwitcher(u, FakeLimits({"Abinaya": 1, "Monika": 50, "Dhamu": 20}), None, dict(TOKENS), None, [], 90)
    assert asyncio.run(sw.pick_best()) is None and u.active == "Monika"  # the quiet background check leaves a fine choice alone
    assert asyncio.run(sw.check()) is None and u.active == "Monika"  # 50% is below the 90% switch point


def test_a_bad_or_unreadable_state_file_is_ignored(tmp_path):
    (tmp_path / "state.json").write_text("{not json")
    assert make(tmp_path).active == "Abinaya"
    (tmp_path / "state.json").write_text('{"active": "Nobody"}')  # an account that no longer exists
    assert make(tmp_path).active == "Abinaya"


class FakeLimits(LimitsCache):
    def __init__(self, used):
        super().__init__()
        self.used = used

    async def get(self, http, label, token, force=False):
        return {"fiveHour": {"usedPercent": self.used[label], "resetsAt": 1}, "sevenDay": {"usedPercent": 1, "resetsAt": 2}}


class Mail:
    sent = 0

    async def send_message(self, *a):
        Mail.sent += 1


def test_pick_best_uses_the_account_with_most_room_and_sends_no_email(tmp_path):
    u = make(tmp_path)  # starts on Abinaya
    sw = AccountSwitcher(u, FakeLimits({"Abinaya": 80, "Monika": 10, "Dhamu": 40}), None, dict(TOKENS), Mail(), ["x@y.z"], 90)
    assert asyncio.run(sw.pick_best()) == "Monika" and u.active == "Monika"
    assert Mail.sent == 0  # servers restart often: no e-mail for an automatic pick


def test_pick_best_does_not_shuffle_people_for_a_small_difference(tmp_path):
    u = make(tmp_path, default="Abinaya")
    sw = AccountSwitcher(u, FakeLimits({"Abinaya": 30, "Monika": 20, "Dhamu": 25}), None, dict(TOKENS), None, [], 90)
    assert asyncio.run(sw.pick_best()) is None and u.active == "Abinaya"


def test_automatic_choice_by_an_admin_takes_the_best_account(tmp_path):
    u = make(tmp_path)
    u.set_active("Monika")
    sw = AccountSwitcher(u, FakeLimits({"Abinaya": 40, "Monika": 50, "Dhamu": 20}), None, dict(TOKENS), None, [], 90)
    assert asyncio.run(sw.pick_best(force=True)) == "Dhamu" and u.active == "Dhamu"
