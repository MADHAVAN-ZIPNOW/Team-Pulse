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


def test_stop_sharing_is_remembered_and_nothing_picks_for_everyone_afterwards(tmp_path):
    u = make(tmp_path)
    u.set_active(None)  # admin: "Stop sharing: let each person choose"
    assert u.active is None and u.sharing_off
    again = make(tmp_path)
    assert again.active is None  # still off after a restart


def test_choosing_an_account_turns_sharing_back_on(tmp_path):
    u = make(tmp_path)
    u.set_active(None)
    u.set_active("Dhamu")
    assert u.active == "Dhamu" and not u.sharing_off


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


def test_pick_best_respects_stop_sharing(tmp_path):
    u = make(tmp_path)
    u.set_active(None)
    sw = AccountSwitcher(u, FakeLimits({"Abinaya": 5, "Monika": 50, "Dhamu": 50}), None, dict(TOKENS), None, [], 90)
    assert asyncio.run(sw.pick_best()) is None and u.active is None
