from den_mail.html.compose import forward_subject, parse_address_list, quote_text, reply_subject, text_to_html
from den_mail.html.sanitize import BLOCKED_PIXEL, sanitize_html
from den_mail.html.totext import html_to_markup, html_to_text, quote_layout, split_quoted_text


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


def test_sanitizer_tags_quoted_history():
    gmail = '<div dir="ltr">Thanks!</div><div class="gmail_quote"><div>On Mon, Anna wrote:</div>' \
            '<blockquote class="gmail_quote">old</blockquote></div>'
    res = sanitize_html(gmail)
    assert res.has_quotes and 'class="gmail_quote den-quote"' in res.html
    assert ".den-quote { display: none !important; }" in res.html and "<body>" in res.html
    assert '<body class="den-show-quotes">' in sanitize_html(gmail, show_quotes=True).html
    assert not sanitize_html(gmail, fold_quotes=False).has_quotes  # an inline reply: nothing folds
    apple = "<div>Yes.</div><div>On 3 Sep 2026, at 10:00, Ben wrote:<br><blockquote type=\"cite\">old</blockquote></div>"
    res = sanitize_html(apple)
    assert res.has_quotes and 'type="cite" class="den-quote"' in res.html
    attributed = "<p>Sure.</p><p>Am 03.09.2026 schrieb Anna:</p><blockquote>alt</blockquote>"
    assert sanitize_html(attributed).has_quotes
    newsletter = "<p>News</p><blockquote>A pull quote, not a reply.</blockquote><p>more</p>"
    res = sanitize_html(newsletter)
    assert not res.has_quotes and "den-quote" not in res.html.split("</style>")[1]


def test_sanitizer_keeps_inline_answers_and_quote_only_mail_visible():
    # Gmail inline reply: the answers sit in the wrapper div between its blockquotes
    inline = ('<div dir="ltr"><br></div><div class="gmail_quote"><div class="gmail_attr">On Tue, Ben wrote:</div>'
              '<blockquote class="gmail_quote">Which room?</blockquote><div>Room 2.04, second floor.</div>'
              '<blockquote class="gmail_quote">Should I bring anything?</blockquote><div>Just the laptop.</div></div>')
    res = sanitize_html(inline)
    body = res.html.split("</style>")[1]
    assert res.has_quotes
    assert '<div class="gmail_quote"><div class="gmail_attr">' in body  # the wrapper is not hidden
    assert body.count('<blockquote class="gmail_quote den-quote">') == 2  # only the quoted paragraphs fold
    # everything typed inside the quote, nothing outside it: shown whole rather than blank + pill
    whole = ('<div dir="ltr"><br></div><div class="gmail_quote"><div class="gmail_attr">On Tue, Ben wrote:</div>'
             '<blockquote class="gmail_quote">Which room?<div>Room 2.04.</div></blockquote></div>')
    res = sanitize_html(whole)
    assert not res.has_quotes and "den-quote" not in res.html.split("</style>")[1]
    # a remote image in an unfolded wrapper counts as visible
    res = sanitize_html(inline.replace("<div>Just the laptop.</div>", '<div>Just <img src="https://x/a.png"></div>'))
    assert res.has_remote_content


def test_sanitizer_folds_everything_after_an_outlook_header():
    outlook = ('<div dir="ltr">My reply</div><div id="appendonsend"></div><hr style="width:98%">'
               '<div id="divRplyFwdMsg" dir="ltr"><b>From:</b> Ben<br><b>Sent:</b> Tuesday</div>'
               '<div dir="ltr"><p>The original text</p><img src="https://x/old.gif"></div><div>Sig</div>')
    res = sanitize_html(outlook)
    body = res.html.split("</style>")[1]
    assert res.has_quotes
    assert '<div dir="ltr">My reply</div>' in body  # the reply itself stays visible
    assert '<hr style="width:98%" class="den-quote">' in body or '<hr class="den-quote"' in body
    assert 'id="appendonsend" class="den-quote"' in body and 'id="divRplyFwdMsg" dir="ltr" class="den-quote"' in body
    assert '<div dir="ltr" class="den-quote"><p>The original text</p>' in body and '<div class="den-quote">Sig</div>' in body
    # the only remote image lives in the history: no banner for the visible part
    assert not res.has_remote_content and res.has_remote_in_quotes


def test_split_quoted_text():
    own, quoted = split_quoted_text("Yes, see you there.\n\nOn Mon, 31 Aug 2026, Anna Berger wrote:\n> are we on?\n>\n> Anna\n")
    assert own == "Yes, see you there." and quoted.startswith("On Mon") and quoted.endswith("> Anna")
    own, quoted = split_quoted_text("Ok\n\nOn Tue, 1 Sep 2026 at 10:00, Anna Berger\n<anna@example.net> wrote:\n> hi")
    assert own == "Ok" and quoted.startswith("On Tue")
    own, quoted = split_quoted_text("Sure\n-----Original Message-----\n> old")
    assert own == "Sure" and quoted.startswith("-----")
    # a signature below the quote (Thunderbird) folds with it
    own, quoted = split_quoted_text("Reply\n\nOn 03.09.26 10:00, Anna Berger wrote:\n> quoted\n> more\n\n-- \nFelix")
    assert own == "Reply" and quoted.startswith("On 03.09.26") and quoted.endswith("-- \nFelix")
    # a signature with no quote above it is not history
    assert split_quoted_text("Hello\n\n-- \nFelix") == ("Hello\n\n-- \nFelix", "")
    # the attribution must end with a colon, and a sentence above it is not part of it
    own, quoted = split_quoted_text("That is what I wrote in my last mail\n\nOn Mon, Anna wrote:\n> hi")
    assert own == "That is what I wrote in my last mail" and quoted.startswith("On Mon")
    own, quoted = split_quoted_text("It is what I wrote\n\n> hi\n> there")
    assert own == "It is what I wrote" and quoted == "> hi\n> there"
    own, quoted = split_quoted_text("On the whole, fine.\nAnna Berger wrote:\n> hi")
    assert own == "On the whole, fine." and quoted == "Anna Berger wrote:\n> hi"
    # inline reply: the exchange stays, only the quote it ends with folds
    own, quoted = split_quoted_text("> question one\nanswer one\n> question two\n> and more")
    assert own == "> question one\nanswer one" and quoted == "> question two\n> and more"
    assert split_quoted_text("> question\nanswer\n") == ("> question\nanswer\n", "")
    # a quote and nothing else, or no quote at all: left whole
    assert split_quoted_text("> only quoted\n> lines") == ("> only quoted\n> lines", "")
    assert split_quoted_text("Hello\n\nBye") == ("Hello\n\nBye", "")


def test_quote_layout():
    assert quote_layout("Hello\n\nBye") == "none"
    assert quote_layout("Yes\n\nOn Mon, Anna wrote:\n> hi") == "trailing"
    assert quote_layout("> question one\nanswer one\n> question two") == "inline"
    assert quote_layout("> question\nanswer") == "inline"
