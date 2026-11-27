import asyncio

from app.services import meeting_log
from app.services.meeting_log import ExcelLog, parse_rows

HEADER = ["Date", "Topic / Idea", "Discussion Summary", "Type", "Priority", "Decision / Agreed Outcome",
          "Next Action", "Owner", "Due Date", "Status", "Reference / Link"]


class FakeLog(ExcelLog):
    def __init__(self, table: bool) -> None:
        super().__init__(None, None, "https://x.sharepoint.com/f")  # type: ignore[arg-type]
        self._table = "t1" if table else ""
        self._base = "/b"
        self.calls: list[tuple[str, str, object]] = []

    async def _call(self, method, path, what, body=None):  # type: ignore[no-untyped-def]
        self.calls.append((method, path, body))
        return {"rowCount": 4}


def rows(**kw):
    base = {k: "" for k in meeting_log.KEYS}
    base.update(kw)
    return base


def test_parse_rows_reads_json_and_drops_junk():
    out = parse_rows('Sure!\n[{"topic": "Website", "status": "In Progress", "x": 1}, {"topic": ""}, 5]')
    assert [r["topic"] for r in out] == ["Website"] and out[0]["status"] == "In Progress"
    assert parse_rows("no json here") == []
    assert parse_rows("[not json]") == []


def test_new_topic_is_added_in_the_right_columns():
    log = FakeLog(table=True)
    existing = [["24-Nov-2026", "Website", "", "Action Item", "High", "", "", "", "", "In Progress", ""]]
    added, updated = asyncio.run(log.write(HEADER, existing, [rows(topic="Deploy plan", summary="Plan it", type="Action Item", priority="High")], "26-Nov-2026"))
    assert (added, updated) == (1, 0)
    method, path, body = log.calls[0]
    assert method == "POST" and path.endswith("/rows/add")
    v = body["values"][0]  # type: ignore[index]
    assert v[0] == "26-Nov-2026" and v[1] == "Deploy plan" and v[2] == "Plan it" and v[4] == "High"


def test_known_topic_is_updated_and_never_blanked():
    log = FakeLog(table=True)
    existing = [["24-Nov-2026", "Website", "old summary", "Action Item", "High", "", "", "Madhavan", "", "In Progress", "https://l"]]
    added, updated = asyncio.run(log.write(HEADER, existing, [rows(topic="website", status="Completed", owner="")], "26-Nov-2026"))
    assert (added, updated) == (0, 1)
    method, path, body = log.calls[0]
    assert method == "PATCH" and "itemAt(index=0)" in path
    v = body["values"][0]  # type: ignore[index]
    assert v[9] == "Completed" and v[7] == "Madhavan" and v[10] == "https://l" and v[1] == "Website"


def test_nothing_changed_means_no_write():
    log = FakeLog(table=True)
    existing = [["d", "Website", "", "Action Item", "High", "", "", "", "", "Completed", ""]]
    assert asyncio.run(log.write(HEADER, existing, [rows(topic="Website", status="Completed")], "d")) == (0, 0)
    assert log.calls == []


def test_without_a_table_it_writes_cell_ranges():
    log = FakeLog(table=False)
    asyncio.run(log.write(HEADER, [], [rows(topic="New")], "26-Nov-2026"))
    method, path, _ = log.calls[-1]
    assert method == "PATCH" and "A2:K2" in path


def test_sheet_mode_ignores_empty_formatted_rows_and_never_overwrites_a_new_row():
    class Sheet(FakeLog):
        async def _call(self, method, path, what, body=None):  # type: ignore[no-untyped-def]
            self.calls.append((method, path, body))
            if "usedRange" in path:  # 3 filled rows, then 500 empty formatted rows (like the real sheet)
                filled = [HEADER, ["d", "A", *[""] * 9], [""] * 11, ["d", "B", *[""] * 9]]
                return {"values": filled + [[""] * 11] * 500}
            return {}

    log = Sheet(table=False)
    header, existing = asyncio.run(log.read())
    asyncio.run(log.write(header, existing, [rows(topic="N1"), rows(topic="N2"), rows(topic="B", status="Done")], "d"))
    addrs = [p.split("address='")[1].split("'")[0] for _, p, _ in log.calls if "address=" in p]
    assert addrs == ["A4:K4", "A5:K6"]  # the update hits B's real row (4), not 3; both new rows go in ONE call right under the data


def test_missing_topic_column_is_a_clear_error():
    log = FakeLog(table=True)
    try:
        asyncio.run(log.write(["A", "B"], [], [rows(topic="x")], "d"))
    except Exception as exc:  # noqa: BLE001
        assert "Topic" in str(exc)
    else:
        raise AssertionError("expected an error")


def test_the_prompt_keeps_stories_and_finds_the_ideas_inside_them():
    text = meeting_log.PROMPT.format(date="26-Nov-2026", existing="(none)", keys=", ".join(meeting_log.KEYS), transcript="x")
    assert "DO NOT SKIP THEM" in text and 'Topic "Story:' in text and 'Topic starts with "Idea: "' in text
    assert "IGNORE" not in text and "get no row" not in text
    for part in ("IDEAS:", "DECISIONS:", "ACTION ITEMS:", "IMPORTANT DISCUSSION POINTS:", "Ignore greetings"):
        assert part in text
