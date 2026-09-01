"""Tests for provider event-link generation."""

import unittest
from datetime import date, time
from urllib.parse import parse_qs, urlparse

from calendar_links import build_calendar_links, infer_timezone_name


class CalendarLinkTests(unittest.TestCase):
    def test_aoe_is_inferred(self) -> None:
        self.assertEqual(infer_timezone_name("AoE (UTC-12:00)"), "Etc/GMT+12")

    def test_missing_timezone_is_not_guessed(self) -> None:
        self.assertIsNone(infer_timezone_name("Not specified"))

    def test_provider_links_contain_timezone_aware_times(self) -> None:
        links = build_calendar_links(
            title="FSE 2027: Full paper submission",
            event_date=date(2026, 10, 2),
            event_time=time(23, 59),
            timezone_name="Etc/GMT+12",
            description="Research Papers",
            source_url="https://example.com/deadline",
        )

        google = parse_qs(urlparse(links["google"]).query)
        outlook = parse_qs(urlparse(links["outlook"]).query)
        self.assertEqual(google["dates"], ["20261003T115900Z/20261003T120000Z"])
        self.assertEqual(google["ctz"], ["Etc/GMT+12"])
        self.assertEqual(outlook["startdt"], ["2026-10-02T23:59:00-12:00"])
        self.assertIn("https://example.com/deadline", outlook["body"][0])

    def test_invalid_timezone_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "time zone"):
            build_calendar_links(
                title="Deadline",
                event_date=date(2026, 10, 2),
                event_time=time(23, 59),
                timezone_name="Not/A-Timezone",
            )


if __name__ == "__main__":
    unittest.main()
