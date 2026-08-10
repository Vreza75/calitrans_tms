import base64

from ui_components.pdf_preview import build_pdf_preview_html


def test_embeds_base64_encoded_pdf_bytes_in_iframe_src():
    html = build_pdf_preview_html(b"%PDF-1.4 fake pdf bytes", height=500)
    expected_b64 = base64.b64encode(b"%PDF-1.4 fake pdf bytes").decode("utf-8")
    assert f"data:application/pdf;base64,{expected_b64}" in html
    assert "<iframe" in html
    assert 'height="500"' in html
