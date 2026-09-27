"""Reading a YouTube video into a record.

The parser is the part with teeth: it decides which URLs get handed to a model
as a `file_uri`, and a permissive one would let a URL on another host through
on the strength of looking vaguely like YouTube.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_mem import youtube  # noqa: E402

VID = "aircAruvnKk"


@pytest.mark.parametrize("url", [
    f"https://www.youtube.com/watch?v={VID}",
    f"http://youtube.com/watch?v={VID}",
    f"https://m.youtube.com/watch?v={VID}",
    f"https://www.youtube.com/watch?v={VID}&t=42s&list=PLabc",
    f"https://youtu.be/{VID}",
    f"https://youtu.be/{VID}?t=42",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://www.youtube.com/embed/{VID}",
    f"https://www.youtube.com/live/{VID}",
    f"https://www.youtube-nocookie.com/embed/{VID}",
])
def test_every_shape_youtube_publishes_resolves_to_one_id(url):
    """People paste whatever the share button gave them."""
    assert youtube.video_id(url) == VID


@pytest.mark.parametrize("url", [
    "",
    "not a url",
    "https://example.com/watch?v=aircAruvnKk",       # right shape, wrong host
    "https://youtube.com.evil.example/watch?v=aircAruvnKk",   # suffix, not host
    "https://www.youtube.com/watch",                  # no id
    "https://www.youtube.com/watch?v=short",          # not 11 characters
    "https://www.youtube.com/watch?v=aircAruvnKk!!",  # not base64url
    "https://www.youtube.com/@3blue1brown",           # a channel, not a video
    "file:///etc/passwd",
    "http://169.254.169.254/watch?v=aircAruvnKk",
])
def test_anything_that_is_not_a_youtube_video_is_refused(url):
    """None, not a guess. Whatever comes back from here is handed to a model as
    a URL to go and fetch, so `looks close enough` is not a property worth
    having."""
    assert youtube.video_id(url) is None


def test_a_host_that_merely_ends_in_youtube_com_is_not_youtube():
    """The check is on the parsed hostname, not on the string containing
    `youtube.com` — which `https://youtube.com.evil.example/` does."""
    assert youtube.video_id("https://youtube.com.evil.example/watch?v=" + VID) is None
    assert youtube.video_id("https://notyoutube.com/watch?v=" + VID) is None


def test_the_same_video_pasted_three_ways_is_one_url():
    """`external_id` is the canonical URL, so three share links must not become
    three records of the same video."""
    shapes = [f"https://youtu.be/{VID}?t=9",
              f"https://www.youtube.com/watch?v={VID}&list=PLx",
              f"https://m.youtube.com/shorts/{VID}"]
    assert {youtube.canonical(youtube.video_id(u)) for u in shapes} == {
        f"https://www.youtube.com/watch?v={VID}"
    }


def test_a_refusal_to_reproduce_the_video_says_so():
    """`RECITATION` is the model declining to write the video out, not a broken
    request — and a reader told only "no content" goes hunting for a bug."""
    with pytest.raises(youtube.ReadUnavailable) as caught:
        youtube._text_of({"candidates": [{"finishReason": "RECITATION", "content": {}}]})
    assert "reproduce" in str(caught.value)


def test_an_empty_answer_is_an_error_rather_than_an_empty_record():
    """An empty account stored as a success is a record that looks ingested and
    answers nothing."""
    with pytest.raises(youtube.ReadUnavailable):
        youtube._text_of({"candidates": []})
    with pytest.raises(youtube.ReadUnavailable):
        youtube._text_of({"candidates": [{"finishReason": "STOP",
                                          "content": {"parts": [{"text": "   "}]}}]})


def test_the_account_is_returned_when_there_is_one():
    assert youtube._text_of(
        {"candidates": [{"finishReason": "STOP",
                         "content": {"parts": [{"text": "a"}, {"text": "b"}]}}]}
    ) == "ab"


def test_the_reader_is_off_without_media_interpretation():
    """Watching a video is media interpretation. A deployment that turned that
    off has not agreed to pay for this either."""
    class S:
        media_interpretation = False
        gemini_api_key = "k"
        multimodal_model = "m"

    assert youtube.build_video_reader(S()) is None
    S.media_interpretation, S.gemini_api_key = True, ""
    assert youtube.build_video_reader(S()) is None
    S.gemini_api_key = "k"
    assert youtube.build_video_reader(S()) is not None
