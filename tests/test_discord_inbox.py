"""디스코드 입력: 링크 추출, 새 메시지 선별, 토큰 파일. 디스코드는 호출하지 않는다."""
import pytest

from ytbook import discord_inbox as di


def msg(mid, author, content="", embeds=(), reply_to=None):
    m = {"id": mid, "author": {"id": author, "username": f"u{author}"}, "content": content, "embeds": list(embeds)}
    if reply_to:
        m["message_reference"] = {"message_id": reply_to}
    return m


def test_video_ids_from_content_and_embeds():
    m = msg("1", "a", "이거 봐 https://youtu.be/dQw4w9WgXcQ?si=abc 그리고 https://www.youtube.com/shorts/abcdefghijk",
            embeds=[{"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}, {"url": "https://m.youtube.com/watch?v=ZZZZZZZZZZZ"}])
    assert di.video_ids(m) == ["dQw4w9WgXcQ", "abcdefghijk", "ZZZZZZZZZZZ"]


def test_video_ids_ignores_other_links_and_bare_text():
    assert di.video_ids(msg("1", "a", "https://example.com/watch?v=dQw4w9WgXcQ 그리고 dQw4w9WgXcQ")) == []
    assert di.video_ids(msg("1", "a", "https://www.youtube.com/@channel")) == []


class FakeDiscord:
    def __init__(self, msgs):
        self.msgs, self.reactions, self.replies = msgs, [], []

    def me(self):
        return {"id": "bot", "username": "yt-curator"}

    def all_after(self, channel_id, after=None):
        return sorted((m for m in self.msgs if after is None or int(m["id"]) > int(after)), key=lambda m: int(m["id"]))

    def react(self, channel_id, message_id, emoji):
        self.reactions.append((message_id, emoji))

    def unreact(self, channel_id, message_id, emoji):  # 디스코드처럼 없는 반응을 지워도 오류가 아니다
        self.reactions = [r for r in self.reactions if r != (message_id, emoji)]

    def reply(self, channel_id, message_id, text):
        self.replies.append((message_id, text))


def test_inbox_skips_bot_replies_and_linkless_and_read_messages():
    dc = FakeDiscord([
        msg("10", "bobuser", "https://youtu.be/AAAAAAAAAAA"),
        msg("12", "bot", "✅ https://youtu.be/AAAAAAAAAAA", reply_to="10"),
        msg("11", "daughter", "안녕"),
        msg("13", "daughter", "https://youtu.be/BBBBBBBBBBB"),
        msg("14", "bot", "https://www.youtube.com/watch?v=CCCCCCCCCCC"),  # tmp 에서 옮긴 링크
    ])
    got = di.inbox(dc, "ch", None, "bot")
    assert [(x["message_id"], x["author_id"], x["video_ids"]) for x in got] == [
        ("10", "bobuser", ["AAAAAAAAAAA"]), ("13", "daughter", ["BBBBBBBBBBB"]), ("14", "bot", ["CCCCCCCCCCC"])]
    assert [x["message_id"] for x in di.inbox(dc, "ch", "10", "bot")] == ["13", "14"]


def test_poll_and_notify(tmp_path):
    dc = FakeDiscord([
        msg("10", "bobuser", "https://youtu.be/AAAAAAAAAAA"),
        msg("11", "daughter", "https://youtu.be/BBBBBBBBBBB https://youtu.be/AAAAAAAAAAA"),
    ])
    ib = di.Inbox(tmp_path)
    assert ib.poll(dc, "ch") == 3
    assert [x["video_id"] for x in ib.items()] == ["AAAAAAAAAAA", "BBBBBBBBBBB"]  # 같은 동영상은 하나만
    assert dc.reactions == [("10", "👀"), ("11", "👀")]
    assert di.Inbox(tmp_path).poll(dc, "ch") == 0  # 이미 읽은 메시지는 다시 넣지 않는다

    done = {"AAAAAAAAAAA": {"title": "제목", "category": "0099_기타", "rating": 4}}
    n = ib.notify(dc, "ch", done, {"BBBBBBBBBBB": "자막 없음"}, set(), "https://example.pages.dev/")
    assert n == 3
    assert ("10", "✅") in dc.reactions and ("10", "👀") not in dc.reactions
    assert dc.replies[0] == ("10", "✅ 제목\n기타 · 별점 4/5\nhttps://example.pages.dev/v/AAAAAAAAAAA.html")
    assert ("11", "❌ 요약하지 못했습니다: 자막 없음") in dc.replies
    assert di.Inbox(tmp_path).notify(dc, "ch", done, {"BBBBBBBBBBB": "자막 없음"}, set(), "x") == 0  # 한 번만 알린다


def test_snowflake_time():
    assert int(di.snowflake_time("175928847299117063")) == 1462015105  # 디스코드 문서의 예


def test_load_token(tmp_path):
    f = tmp_path / "discord.env"
    with pytest.raises(di.DiscordError, match="토큰 파일 없음"):
        di.load_token(f)
    f.write_text("# 주석\nDISCORD_BOT_TOKEN='abc.def'\n")
    assert di.load_token(f) == "abc.def"
    f.write_text("OTHER=1\n")
    with pytest.raises(di.DiscordError, match="DISCORD_BOT_TOKEN"):
        di.load_token(f)
