"""Regression tests for conference and track normalization."""

import unittest

from deadline_cleaner import classify_track, normalize_conference_name


class ConferenceNameTests(unittest.TestCase):
    def test_bad_page_title_uses_known_slug(self) -> None:
        self.assertEqual(normalize_conference_name("Welcome!", "eseiw-2026"), "ESEIW 2026")

    def test_known_conferences_use_consistent_short_names(self) -> None:
        self.assertEqual(
            normalize_conference_name("Mining Software Repositories 2027", "msr-2027"),
            "MSR 2027",
        )


class TrackCategoryTests(unittest.TestCase):
    CASES = {
        "FSE Industry Papers": "Industry",
        "New Ideas and Emerging Results (NIER)": "New Ideas & Emerging Results",
        "Reproducibility Studies and Negative Results (RENE) Track": "Reflections & Negative Results",
        "ESEM - Technical Track ESEIW 2026": "Research",
        "Posters and Tool Demos": "Tools, Demos & Data",
        "Short Papers and Posters (SP&P) Track": "Short Papers & Posters",
        "Registered Report (RR) Track": "Registered Reports",
        "Software Engineering Education and Training (SEET)": "Education",
        "Student Mentoring Workshop (SMeW)": "Mentoring & Community",
        "Workshops & Tutorials (W&T)": "Workshops & Tutorials",
    }

    def test_representative_track_names(self) -> None:
        for track, expected in self.CASES.items():
            with self.subTest(track=track):
                self.assertEqual(classify_track(track), expected)


if __name__ == "__main__":
    unittest.main()
