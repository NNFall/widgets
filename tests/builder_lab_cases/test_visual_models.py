import unittest

from builder_lab.visual_models import (
    LayoutEvidence,
    LayoutState,
    NormalizedRegion,
    RegionEvidence,
    ScreenshotEvidence,
    ScreenshotState,
    VisualCategory,
    VisualCritique,
    VisualFinding,
    VisualSeverity,
    VisualVerdict,
)


def finding(**changes) -> VisualFinding:
    payload = {
        "finding_id": "finding-1",
        "severity": VisualSeverity.MINOR,
        "category": VisualCategory.SITE_FIT,
        "screenshot_id": "desktop-open",
        "evidence": "The hairline is visibly heavier than the reference.",
        "region": NormalizedRegion(
            x=0.72,
            y=0.61,
            width=0.24,
            height=0.34,
            semantic_region="panel",
        ),
        "artifact_fields": ("css",),
        "repair_instruction": "Reduce the panel border to one CSS pixel.",
        "confidence": 0.82,
    }
    payload.update(changes)
    return VisualFinding(**payload)


class VisualEvidenceModelTests(unittest.TestCase):
    def test_screenshot_contract_enforces_state_hash_dimensions_and_inline_budget(self):
        shot = ScreenshotEvidence(
            screenshot_id="desktop-open",
            state=ScreenshotState.DESKTOP_OPEN_INITIAL,
            sha256="a" * 64,
            mime_type="image/jpeg",
            byte_count=1_500_000,
            width=1440,
            height=900,
        )
        self.assertEqual(ScreenshotEvidence.from_dict(shot.to_dict()), shot)
        for changes in (
            {"screenshot_id": 123},
            {"sha256": "not-a-hash"},
            {"sha256": int("1" * 64)},
            {"mime_type": 42},
            {"byte_count": 1_500_001},
            {"byte_count": "1"},
            {"state": "desktop.unknown"},
            {"width": 0},
            {"width": 1440.9},
            {"height": True},
        ):
            payload = shot.to_dict()
            payload.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                ScreenshotEvidence.from_dict(payload)

    def test_layout_contract_rejects_duplicate_regions_and_unbounded_metrics(self):
        region = RegionEvidence(
            region="panel",
            x=20,
            y=20,
            width=372,
            height=304,
            client_width=372,
            scroll_width=372,
            client_height=304,
            scroll_height=304,
            visible=True,
        )
        layout = LayoutEvidence(
            evidence_id="layout-desktop-open",
            state=LayoutState.DESKTOP_OPEN_INITIAL,
            screenshot_id="desktop-open",
            viewport_width=1440,
            viewport_height=900,
            regions=(region,),
            horizontal_overflow_px=0.5,
            panel_inside_viewport=True,
            visible_action_count=3,
            transcript_roles=("assistant", "user"),
            aria_states=("launcher.aria-expanded=true", "panel.aria-hidden=false"),
        )
        self.assertEqual(LayoutEvidence.from_dict(layout.to_dict()), layout)
        self.assertIn("panel.aria-hidden=false", layout.aria_states)
        self.assertEqual(layout.visible_action_count, 3)
        with self.assertRaises(ValueError):
            LayoutEvidence(
                evidence_id="layout-duplicate",
                state=LayoutState.DESKTOP_OPEN_INITIAL,
                screenshot_id="desktop-open",
                viewport_width=1440,
                viewport_height=900,
                regions=(region, region),
            )
        with self.assertRaises(ValueError):
            LayoutEvidence(
                evidence_id="layout-unbounded",
                state=LayoutState.DESKTOP_OPEN_INITIAL,
                screenshot_id="desktop-open",
                viewport_width=20_001,
                viewport_height=900,
            )
        for changes in (
            {"panel_inside_viewport": "false"},
            {"viewport_width": "1440"},
            {"viewport_height": 900.5},
            {"active_element": 42},
            {"visible_action_count": "3"},
            {"visible_action_count": 101},
            {"transcript_roles": ["assistant", 7]},
        ):
            payload = layout.to_dict()
            payload.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                LayoutEvidence.from_dict(payload)
        with self.assertRaises(ValueError):
            RegionEvidence.from_dict({**region.to_dict(), "visible": "false"})

    def test_layout_after_turn_one_has_its_own_state_without_a_seventh_screenshot(self):
        layout = LayoutEvidence(
            evidence_id="layout-desktop-after-turn-1",
            state=LayoutState.DESKTOP_AFTER_TURN_1,
            screenshot_id=None,
            viewport_width=1440,
            viewport_height=900,
        )
        self.assertEqual(LayoutEvidence.from_dict(layout.to_dict()), layout)
        with self.assertRaises(ValueError):
            LayoutEvidence(
                evidence_id="layout-missing-shot",
                state=LayoutState.DESKTOP_OPEN_INITIAL,
                screenshot_id=None,
                viewport_width=1440,
                viewport_height=900,
            )
        with self.assertRaises(ValueError):
            LayoutEvidence(
                evidence_id="layout-extra-shot",
                state=LayoutState.DESKTOP_AFTER_TURN_1,
                screenshot_id="seventh-screenshot",
                viewport_width=1440,
                viewport_height=900,
            )

    def test_finding_enforces_enums_bounds_and_stable_semantic_fingerprint(self):
        first = finding(finding_id="finding-1")
        same_semantics = finding(finding_id="finding-renumbered")
        self.assertEqual(first.fingerprint, same_semantics.fingerprint)
        with self.assertRaises(ValueError):
            finding(confidence=1.01)
        report_only = finding(severity=VisualSeverity.MAJOR, confidence=0.74)
        report = VisualCritique(
            verdict=VisualVerdict.PASS,
            summary="Low-confidence major remains report-only.",
            findings=(report_only,),
        )
        self.assertFalse(report.requires_repair)
        with self.assertRaises(ValueError):
            VisualFinding.from_dict({**first.to_dict(), "severity": "critical"})
        with self.assertRaises(ValueError):
            VisualFinding.from_dict({**first.to_dict(), "evidence": 404})
        invalid_region = first.to_dict()
        invalid_region["region"]["semantic_region"] = 7
        with self.assertRaises(ValueError):
            VisualFinding.from_dict(invalid_region)
        with self.assertRaises(ValueError):
            finding(artifact_fields=("css",) * 5)
        with self.assertRaises(ValueError):
            finding(region=NormalizedRegion(x=0.9, y=0.1, width=0.2, height=0.2))

    def test_critique_requires_unique_ids_and_pass_without_blocker_or_major(self):
        minor = finding()
        passed = VisualCritique(
            verdict=VisualVerdict.PASS,
            summary="All six signed states retain the RAW editorial grammar.",
            findings=(minor,),
        )
        self.assertFalse(passed.requires_repair)
        self.assertEqual(VisualCritique.from_dict(passed.to_dict()), passed)
        self.assertEqual(passed.fingerprint, VisualCritique.from_dict(passed.to_dict()).fingerprint)
        with self.assertRaises(ValueError):
            VisualCritique.from_dict({**passed.to_dict(), "summary": 123})
        with self.assertRaises(ValueError):
            VisualCritique(
                verdict=VisualVerdict.REPAIR,
                summary="duplicate ids",
                findings=(minor, minor),
            )
        with self.assertRaises(ValueError):
            VisualCritique(
                verdict=VisualVerdict.PASS,
                summary="must not pass",
                findings=(finding(severity=VisualSeverity.MAJOR),),
            )
        with self.assertRaises(ValueError):
            VisualCritique(
                verdict=VisualVerdict.REPAIR,
                summary="too many",
                findings=tuple(finding(finding_id=f"finding-{index}") for index in range(13)),
            )
        with self.assertRaises(ValueError):
            VisualCritique(
                verdict=VisualVerdict.REPAIR,
                summary="minor-only findings do not trigger repair",
                findings=(minor,),
            )


if __name__ == "__main__":
    unittest.main()
