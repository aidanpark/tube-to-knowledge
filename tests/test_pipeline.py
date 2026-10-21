"""파이프라인: 차단 시 쉬는 시간, 디스코드에 알릴 결과. YouTube · claude · git · 디스코드는 호출하지 않는다."""
import json

from ytbook import backlog, pipeline


def test_pause_escalates_and_resets(tmp_path):
    st = pipeline.Status(tmp_path / "status.json")
    st.blocked(1000)
    assert st.d["pause_until"] == 1000 + 1800 and st.paused(1001) and not st.paused(2801)
    for t in (3000, 6000, 9000, 20000):
        st.blocked(t)
    assert st.d["pause_until"] == 20000 + 14400  # 최대 4시간
    assert st.d["blocked_since"] == 1000
    st.unblocked()
    assert not st.paused(20001) and "blocked_since" not in st.d
    st.save()
    assert "last_run" in json.loads((tmp_path / "status.json").read_text())


def test_results_for_discord(tmp_path):
    summaries, state = tmp_path / "summaries", tmp_path / "state"
    (summaries / "0099_기타").mkdir(parents=True)
    (summaries / "0099_기타" / "2026-10-20_AAAAAAAAAAA.md").write_text('---\ntitle: "제목"\n---\n')
    st = backlog.State(state)
    st.fail({"video_id": "BBBBBBBBBBB"}, "no_transcript", "")
    st.fail({"video_id": "AAAAAAAAAAA"}, "transient", "")  # 나중에 요약했으면 요약으로 본다
    (state / "removed.jsonl").write_text('{"video_id": "CCCCCCCCCCC"}\n')
    done, failed, removed = pipeline.results({"AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC", "DDDDDDDDDDD"}, summaries, st)
    assert done == {"AAAAAAAAAAA": {"title": "제목"}}
    assert failed == {"BBBBBBBBBBB": "자막 없음"}
    assert removed == {"CCCCCCCCCCC"}
