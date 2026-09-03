from fastmail_gtk.html.compose import forward_subject, parse_address_list, quote_text, reply_subject, text_to_html
from fastmail_gtk.html.sanitize import BLOCKED_PIXEL, sanitize_html
from fastmail_gtk.html.totext import html_to_markup, html_to_text


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
