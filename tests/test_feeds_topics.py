from app.services.feeds.topics import load_registry
from app.config import settings


def test_default_registry_has_expected_topics():
    reg = load_registry(settings.feeds_topics_file)
    expected = {"navier_stokes", "p_vs_np", "riemann", "bsd", "hodge", "yang_mills"}
    assert expected.issubset(set(reg.names))


def test_topic_membership_and_lookup(tmp_path):
    yaml_path = tmp_path / "topics.yaml"
    yaml_path.write_text(
        "topics:\n"
        "  demo:\n"
        "    arxiv: [cs.LG]\n"
        "    reddit: [MachineLearning]\n"
        "    hn: ['llm']\n",
        encoding="utf-8",
    )
    reg = load_registry(yaml_path)
    assert "demo" in reg
    assert reg.get("demo")["arxiv"] == ["cs.LG"]
    assert reg.get("missing") is None
