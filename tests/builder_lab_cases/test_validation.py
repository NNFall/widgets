import unittest

from builder_lab.models import Stage, WidgetArtifact
from builder_lab.validation import issue_fingerprint, validate_artifact


GOOD_HTML = """
<section class="kaigo-widget" data-region="root" aria-label="AI-консультант">
  <button class="kaigo-widget__launcher" data-region="launcher" aria-label="Открыть AI-консультанта" type="button"><span>AI</span></button>
  <div class="kaigo-widget__panel" data-region="panel" role="dialog" aria-label="Диалог с AI-консультантом">
    <header class="kaigo-widget__header" data-region="header"><h2>Мария — AI-консультант</h2></header>
    <main class="kaigo-widget__messages" data-region="messages" aria-live="polite"><p>Чем помочь?</p></main>
    <div class="kaigo-widget__suggestions" data-region="suggestions"><button type="button">Подобрать решение</button></div>
    <div class="kaigo-widget__composer" data-region="composer" role="group" aria-label="Сообщение"><input aria-label="Введите сообщение"><button type="button">Отправить</button></div>
  </div>
</section>
"""

GOOD_CSS = """
.kaigo-widget { --accent: #f38b55; color: #18212b; position: relative; }
.kaigo-widget .kaigo-widget__panel { animation: kaigo-rise 600ms ease-out 1 both; }
@keyframes kaigo-rise { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
@media (prefers-reduced-motion: reduce) { .kaigo-widget * { animation: none !important; transition: none !important; } }
"""


def artifact(**changes):
    payload = {
        "schema_version": "1.0",
        "revision": 2,
        "stage": Stage.CONVERSATION,
        "art_direction": "Warm editorial concierge with a sunrise motif",
        "body_html": GOOD_HTML,
        "css": GOOD_CSS,
        "theme_tokens": {"accent": "#f38b55"},
        "suggested_actions": ("Подобрать решение",),
    }
    payload.update(changes)
    return WidgetArtifact(**payload)


class ArtifactValidationTests(unittest.TestCase):
    def codes(self, candidate, previous_revision=1):
        return [
            issue.code
            for issue in validate_artifact(candidate, previous_revision=previous_revision)
        ]

    def test_accepts_safe_complete_artifact(self):
        self.assertEqual(validate_artifact(artifact(), previous_revision=1), ())

    def test_accepts_safe_native_controls_and_svg_presentation_attributes(self):
        native_controls = """
        <details class="kaigo-widget__details" open>
          <summary class="kaigo-widget__summary">Параметры проекта</summary>
          <label class="kaigo-widget__label" for="kaigo-email">Почта</label>
          <input id="kaigo-email" name="email" type="checkbox" checked autocomplete="off">
          <svg viewBox="0 0 24 24"><line x1="2" y1="12" x2="22" y2="12" stroke-dasharray="4 2" stroke-dashoffset="1"></line></svg>
        </details>
        """
        candidate = artifact(
            body_html=GOOD_HTML.replace("</section>", native_controls + "</section>")
        )
        self.assertEqual(validate_artifact(candidate, previous_revision=1), ())

    def test_rejects_schema_and_non_monotonic_revision(self):
        self.assertIn("unsupported_schema", self.codes(artifact(schema_version="2.0")))
        self.assertIn("non_monotonic_revision", self.codes(artifact(revision=1)))

    def test_requires_semantic_regions_and_accessible_labels(self):
        candidate = artifact(
            body_html=GOOD_HTML.replace(' data-region="composer"', "").replace(
                'aria-label="Открыть AI-консультанта"', ""
            )
        )
        codes = self.codes(candidate)
        self.assertIn("missing_region", codes)
        self.assertIn("missing_accessible_label", codes)

    def test_rejects_forbidden_elements_and_event_handlers(self):
        candidate = artifact(
            body_html=GOOD_HTML.replace(
                "</section>", '<script>alert(1)</script><div onclick="alert(1)"></div></section>'
            )
        )
        codes = self.codes(candidate)
        self.assertIn("forbidden_element", codes)
        self.assertIn("forbidden_attribute", codes)

    def test_rejects_svg_arc_paths_that_can_fail_in_the_browser(self):
        candidate = artifact(
            body_html=GOOD_HTML.replace(
                "</section>",
                '<svg viewBox="0 0 24 24"><path d="M0 0 A9 9 0 009 9"></path></svg></section>',
            )
        )
        self.assertIn("unsafe_svg_path", self.codes(candidate))

    def test_rejects_duplicate_attributes_before_url_validation(self):
        candidate = artifact(
            body_html=GOOD_HTML.replace(
                "</section>",
                '<a href="javascript:alert(1)" href="#safe">x</a></section>',
            )
        )
        self.assertIn("duplicate_attribute", self.codes(candidate))

    def test_rejects_external_urls_forms_and_unsafe_data(self):
        candidate = artifact(
            body_html=GOOD_HTML.replace(
                "</section>",
                '<a href="https://evil.test">x</a><form action="/steal"></form>'
                '<img src="data:text/html;base64,PHNjcmlwdD4="></section>',
            )
        )
        codes = self.codes(candidate)
        self.assertIn("external_url", codes)
        self.assertIn("forbidden_element", codes)
        self.assertIn("unsafe_data_url", codes)

    def test_rejects_malformed_and_oversized_html(self):
        self.assertIn("malformed_html", self.codes(artifact(body_html=GOOD_HTML + "<div>")))
        self.assertIn("html_too_large", self.codes(artifact(body_html="<div>" + ("x" * 70_000) + "</div>")))

    def test_rejects_excessive_dom_nodes(self):
        nodes = "".join("<span>x</span>" for _ in range(450))
        self.assertIn(
            "too_many_nodes",
            self.codes(artifact(body_html=GOOD_HTML.replace("</section>", nodes + "</section>"))),
        )

    def test_rejects_unscoped_and_unsafe_css(self):
        unsafe = GOOD_CSS + "\nbody { color:red }\n.other { color:red }\n@import 'x.css';\n.kaigo-widget{background:url(https://evil.test/x)}"
        codes = self.codes(artifact(css=unsafe))
        self.assertIn("unscoped_css", codes)
        self.assertIn("unsafe_css_at_rule", codes)
        self.assertIn("external_css_resource", codes)

    def test_unscoped_css_issue_names_the_first_offending_selector(self):
        issues = validate_artifact(
            artifact(css=GOOD_CSS + "\nbody, .outside { color: red; }"),
            previous_revision=1,
        )
        issue = next(item for item in issues if item.code == "unscoped_css")

        self.assertIn("body", issue.message)

    def test_rejects_selectors_that_only_mention_widget_without_targeting_it(self):
        for selector in (
            ":not(.kaigo-widget)",
            "*:has(.kaigo-widget)",
            ".outside .kaigo-widget",
            ".kaigo-widget + .outside",
            ".kaigo-widget ~ .outside",
        ):
            css = (
                f"{selector} {{ color: red; }}\n"
                "@media (prefers-reduced-motion: reduce) { "
                ".kaigo-widget * { animation: none; } }"
            )
            with self.subTest(selector=selector):
                self.assertIn("unscoped_css", self.codes(artifact(css=css)))

    def test_rejects_case_insensitive_style_terminator(self):
        unsafe = GOOD_CSS + '\n.kaigo-widget::before { content: "</StYlE><script>bad()</script>"; }'
        self.assertIn("unsafe_style_terminator", self.codes(artifact(css=unsafe)))

    def test_rejects_oversized_stylesheet_and_excessive_motion(self):
        self.assertIn("css_too_large", self.codes(artifact(css=GOOD_CSS + (" " * 100_000))))
        motion = ".kaigo-widget { animation: pulse 30s linear 99; }\n@keyframes pulse {to{opacity:.5}}\n@media (prefers-reduced-motion: reduce){.kaigo-widget *{animation:none}}"
        codes = self.codes(artifact(css=motion))
        self.assertIn("animation_too_long", codes)
        self.assertIn("animation_iterations_exceeded", codes)

    def test_motion_requires_reduced_motion_fallback(self):
        css = GOOD_CSS.split("@media (prefers-reduced-motion", 1)[0]
        self.assertIn("missing_reduced_motion", self.codes(artifact(css=css)))

    def test_fingerprint_is_stable_for_same_issue_set(self):
        first = validate_artifact(artifact(css="body {color:red}"), previous_revision=2)
        second = tuple(reversed(first))
        self.assertEqual(issue_fingerprint(first), issue_fingerprint(second))


if __name__ == "__main__":
    unittest.main()
