import unittest

from builder_lab.preview import PREVIEW_CSP, build_preview_document, preview_iframe_attributes
from tests.builder_lab_cases.test_validation import artifact


class PreviewDocumentTests(unittest.TestCase):
    def test_document_contains_restrictive_csp_and_validated_artifact(self):
        candidate = artifact(revision=7)
        document = build_preview_document(candidate)
        self.assertIn("default-src &#x27;none&#x27;", document)
        self.assertIn("connect-src &#x27;none&#x27;", document)
        self.assertIn("form-action &#x27;none&#x27;", document)
        self.assertIn(candidate.body_html, document)
        self.assertIn(candidate.css, document)
        self.assertNotIn("allow-same-origin", document)
        self.assertNotIn("fetch(", document)
        self.assertNotIn("XMLHttpRequest", document)
        self.assertEqual(PREVIEW_CSP.count("connect-src"), 1)

    def test_fixed_runtime_has_versioned_render_acknowledgement(self):
        document = build_preview_document(artifact(revision=9))
        self.assertIn("kaigo-builder-preview", document)
        self.assertIn("version: 1", document)
        self.assertIn("type: 'rendered'", document)
        self.assertIn("revision: 9", document)
        self.assertIn("event.source", document)
        self.assertNotIn("generated_javascript", document)

    def test_fixed_runtime_synchronizes_css_checkbox_toggles(self):
        document = build_preview_document(artifact(revision=10))
        self.assertIn("input[type=\"checkbox\"]", document)
        self.assertIn("toggle.checked = Boolean(open)", document)
        self.assertIn("toggle.addEventListener('change'", document)

    def test_metadata_is_escaped(self):
        document = build_preview_document(
            artifact(art_direction='</script><script id="escape">bad()</script>')
        )
        self.assertNotIn('<script id="escape">', document)

    def test_style_terminator_is_escaped_case_insensitively(self):
        document = build_preview_document(
            artifact(css='.kaigo-widget { content: "</StYlE><script id=escape>bad()</script>"; }')
        )
        self.assertNotIn("</StYlE>", document)
        self.assertNotIn("</StYlE><script id=escape>", document)
        self.assertIn("<\\/style><script id=escape>", document)

    def test_parent_iframe_contract_is_opaque(self):
        attributes = preview_iframe_attributes()
        self.assertEqual(attributes["sandbox"], "allow-scripts")
        self.assertNotIn("allow-same-origin", attributes["sandbox"])
        self.assertEqual(attributes["referrerpolicy"], "no-referrer")
        self.assertIn("AI", attributes["title"])


if __name__ == "__main__":
    unittest.main()
