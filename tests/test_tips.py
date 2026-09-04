"""The tips under the empty conversation pane."""

from den_mail import tips


def test_tips_rotate_and_name_real_actions():
    assert len(tips.TIPS) >= 10
    assert tips.tip_for(0) is tips.TIPS[0] and tips.tip_for(len(tips.TIPS)) is tips.TIPS[0]
    assert tips.tip_for(3) is tips.TIPS[3]
    for tip in tips.TIPS:
        assert tip.title and tip.text and (tip.action is None) == (not tip.button)
        assert tip.action is None or tip.action.startswith("win.")
