from den_mail.html.compose import forward_subject, parse_address_list, quote_text, reply_subject, text_to_html
from den_mail.html.sanitize import BLOCKED_PIXEL, sanitize_html
from den_mail.html.totext import html_to_markup, html_to_text


def test_sanitizer_strips_scripts_and_gates_remote_images():
    html = '<html><body onload="x()"><script>alert(1)</script><p style="background:url(http://x/y.png)">Hi ' \
           '<a href="javascript:evil()">bad</a> <a href="https://ok.example">ok</a></p>' \
           '<img src="https://track.example/p.gif"><img src="cid:logo@x"><iframe src="https://x"></iframe></body></html>'
    res = sanitize_html(html, allow_remote=False)
    assert "<script" not in res.html and "onload" not in res.html and "iframe" not in res.html
    assert "javascript:" not in res.html
    assert BLOCKED_PIXEL in res.html
    assert res.has_remote_content
    assert "cid:logo%40x" in res.html and res.cids == ["logo@x"]
    allowed = sanitize_html(html, allow_remote=True)
    assert "https://track.example/p.gif" in allowed.html


def test_html_to_text_handles_structure():
    html = "<h1>Title</h1><p>Para <b>bold</b> and <a href='https://e.x'>link</a></p><ul><li>one</li><li>two</li></ul>" \
           "<blockquote>quoted<br>line</blockquote><pre>  code\n  more</pre><style>p{}</style>"
    text = html_to_text(html)
    assert "Title" in text and "Para bold and link <https://e.x>" in text
    assert "• one" in text and "• two" in text
    assert "> quoted" in text and "> line" in text
    assert "  code\n  more" in text
    assert "p{}" not in text


def test_html_to_markup_is_balanced_and_escaped():
    markup = html_to_markup("<p>a &lt; b <b>bold <i>both</b> stray</i></p>")
    assert "a &lt; b" in markup
    assert markup.count("<b>") == markup.count("</b>")
    assert markup.count("<i>") == markup.count("</i>")


def test_compose_helpers():
    assert reply_subject("Hello") == "Re: Hello"
    assert reply_subject("RE: Hello") == "RE: Hello"
    assert forward_subject("Hello") == "Fwd: Hello"
    assert quote_text("a\n\nb") == "> a\n>\n> b"
    html = text_to_html("line1\n> quoted\nhttps://x.example/p")
    assert "<blockquote>" in html and 'href="https://x.example/p"' in html
    addrs = parse_address_list("Anna <anna@example.net>, ben@example.net; Chiara Rossi <c@example.net>")
    assert addrs == [{"name": "Anna", "email": "anna@example.net"}, {"name": None, "email": "ben@example.net"},
                     {"name": "Chiara Rossi", "email": "c@example.net"}]


def test_dark_mode_flips_colours_but_not_images():
    from den_mail.html.darkmode import flip_color_value, flip_css
    from den_mail.html.sanitize import sanitize_html

    assert flip_color_value("#ffffff") != "#ffffff"
    dark_bg = flip_color_value("#ffffff")
    assert int(dark_bg[1:3], 16) < 0x40  # white became dark
    light_text = flip_color_value("rgb(0, 0, 0)")
    assert int(light_text[4:].split(",")[0]) > 0xc0  # black became light
    orange = flip_color_value("#ff7800")
    assert orange.startswith("#")  # mid-lightness colours keep their hue (r > g > b)
    r, g, b = (int(orange[i:i + 2], 16) for i in (1, 3, 5))
    assert r > g > b
    css = flip_css("color: black; background: url(https://x/y.png) white; font-family: Arial Black; width: 10px")
    assert "url(https://x/y.png)" in css and "Arial Black" in css and "width: 10px" in css
    assert "black;" not in css.split("font-family")[0]
    html = '<body bgcolor="#FFFFFF"><p style="color:#111">Hi</p><style>td{background:#fff}</style></body>'
    res = sanitize_html(html, dark=True)
    assert "color-scheme: dark" in res.html and "#111" not in res.html and "#fff}" not in res.html.lower()
    native = sanitize_html('<meta name="color-scheme" content="light dark"><p style="color:#111">x</p>', dark=True)
    assert "#111" in native.html  # message handles dark mode itself
    assert "color-scheme: dark" in native.html


def test_dark_mode_keeps_text_readable_and_backgrounds_dark():
    from den_mail.html.darkmode import flip_css

    css = flip_css("color: #0000ff; background-color: #0000ff")
    text_hex, bg_hex = [m for m in __import__("re").findall(r"#[0-9a-f]{6}", css)]
    import colorsys
    def lightness(h):
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return colorsys.rgb_to_hls(r, g, b)[1]
    assert lightness(text_hex) >= 0.6  # pure blue link becomes a light blue
    assert lightness(bg_hex) <= 0.46  # pure blue background stays dark-ish


def test_assemble_body_handles_part_sequences():
    from den_mail.html.body import assemble_body, find_inline_part
    # Apple Mail: text, inline photo, text; htmlBody mirrors textBody, no text/html
    parts = [{"partId": "1", "type": "text/plain"},
             {"partId": "2", "type": "image/jpeg", "blobId": "b2", "name": "IMG_8163.jpeg", "disposition": "inline"},
             {"partId": "3", "type": "text/plain"}]
    full = {"textBody": parts, "htmlBody": parts,
            "bodyValues": {"1": {"value": "\r\n"}, "3": {"value": "Sent from my iPhone\r\n"}}}
    c = assemble_body(full)
    assert c.has_html is False
    assert 'src="cid:part:2"' in c.html and "Sent from my iPhone" in c.html
    assert c.text.strip() == "Sent from my iPhone"
    assert find_inline_part(full, "part:2")["blobId"] == "b2"
    assert find_inline_part(full, "part:1") is None          # no blob behind a text part
    # plain text only: every text part, not just the first
    c = assemble_body({"textBody": [{"partId": "1", "type": "text/plain"}, {"partId": "2", "type": "text/plain"}],
                       "bodyValues": {"1": {"value": "one"}, "2": {"value": "two"}}})
    assert c.html is None and c.text == "one\ntwo"
    # real html wins, images between html parts are kept
    c = assemble_body({"htmlBody": [{"partId": "h", "type": "text/html"},
                                    {"partId": "i", "type": "image/png", "blobId": "b", "cid": "<pic@x>"}],
                       "textBody": [{"partId": "t", "type": "text/plain"}],
                       "bodyValues": {"h": {"value": "<p>hi</p>", "isTruncated": True}, "t": {"value": "hi"}}})
    assert c.has_html and c.truncated and "<p>hi</p>" in c.html and 'src="cid:pic@x"' in c.html
    # whitespace-only html and no pictures falls back to text
    c = assemble_body({"htmlBody": [{"partId": "h", "type": "text/html"}], "textBody": [{"partId": "t", "type": "text/plain"}],
                       "bodyValues": {"h": {"value": " \n"}, "t": {"value": "plain"}}})
    assert c.html is None and c.text == "plain" and not c.has_html
