"""Blind response review is isolated from provider and native action transport."""
import json

import pytest

from per_pulse import blinded_review as review


@pytest.fixture
def trial(tmp_path, monkeypatch):
    root = tmp_path / "trial"
    root.mkdir()
    names = ("bare_transfer", "conditional_transfer", "bare_negative", "conditional_negative")
    stages = []
    for index, name in enumerate(names):
        filename = name.replace("_", "-") + "-response.md"
        raw = f"Answer {index + 1}: saved files remain readable.".encode()
        (root / filename).write_bytes(raw)
        stages.append({"stage": name, "result_event_id": f"native-{index}",
                       "artifact": {"path": filename, "sha256": review._sha(raw)},
                       "usage": {"accounted": index % 2 == 0}})
    record = {"status": "completed", "origin": "real_provider", "stages": stages,
              "protocol": [{"stage": name, "prompt": "Write an update" if "transfer" in name else "Outline only"}
                           for name in names]}
    (root / "ordinary-transfer.json").write_text(json.dumps(record))
    monkeypatch.setattr(review, "audit_ordinary_transfer", lambda _: {"status": "verified", "errors": []})
    return root, record


def test_blind_packet_separates_labels_and_private_key(trial, tmp_path, monkeypatch):
    root, _ = trial
    flags = iter((0, 1))
    monkeypatch.setattr(review.secrets, "randbelow", lambda _: next(flags))
    result = review.prepare(root, tmp_path / "blind")
    assert result["origin"] == "real_provider" and result["items"] == 4
    packet = review._load_json(tmp_path / "blind" / "reviewer" / "review-packet.json")
    key = review._load_json(tmp_path / "blind" / "private" / "condition-key.json")
    assert [item["label"] for item in packet["items"]] == ["A", "B", "C", "D"]
    assert "conditional" not in json.dumps(packet).lower()
    assert "baseline" not in json.dumps(packet).lower()
    assert key["mapping"]["A"]["condition"] == "baseline"
    assert key["mapping"]["C"]["condition"] == "conditional"
    assert (tmp_path / "blind" / ".gitignore").read_text() == "*\n"
    assert result["reviewer_directory"] == str(tmp_path / "blind" / "reviewer")
    assert result["review_file"] == "reviewer/review-packet.json"
    assert result["private_key_file"] == "private/condition-key.json"
    assert not (tmp_path / "blind" / "reviewer" / "condition-key.json").exists()
    assert set(p.name for p in (tmp_path / "blind" / "reviewer").iterdir()) == {
        "review-packet.json", "ratings-template.json"}


def test_fixture_requires_explicit_qa_opt_in(trial, tmp_path):
    root, record = trial
    record["origin"] = "synthetic_fixture"
    (root / "ordinary-transfer.json").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="fixture"):
        review.prepare(root, tmp_path / "blind")
    assert not (tmp_path / "blind").exists()
    review.prepare(root, tmp_path / "blind", allow_fixture=True)
    key = review._load_json(tmp_path / "blind" / "private" / "condition-key.json")
    assert key["trial_origin"] == "synthetic_fixture"


def test_native_audit_rejects_disconnected_or_tampered_record(trial, tmp_path, monkeypatch):
    root, _ = trial
    monkeypatch.setattr(review, "audit_ordinary_transfer", lambda _: {"status": "invalid", "errors": ["native_trace_mismatch"]})
    with pytest.raises(ValueError, match="native trial audit"):
        review.prepare(root, tmp_path / "blind")
    assert not (tmp_path / "blind").exists()


def test_artifact_must_still_match_its_native_digest(trial, tmp_path):
    root, record = trial
    (root / record["stages"][0]["artifact"]["path"]).write_text("substituted")
    with pytest.raises(ValueError, match="digest mismatch"):
        review.prepare(root, tmp_path / "blind")
    assert not (tmp_path / "blind").exists()


def test_blind_grading_is_complete_and_bound_to_packet(trial, tmp_path):
    root, _ = trial
    folder = tmp_path / "blind"
    review.prepare(root, folder)
    ratings = review._load_json(folder / "reviewer" / "ratings-template.json")
    ratings["reviewer"] = "bounded-human-review"
    for checks in ratings["ratings"].values():
        for check in checks:
            checks[check] = True
    ratings_path = tmp_path / "ratings.json"
    ratings_path.write_text(json.dumps(ratings))
    report = review.grade(folder, ratings_path)
    assert report["trial_origin"] == "real_provider"
    assert set(report["comparisons"]) == {"notice", "outline"}
    assert report["blindness_self_report_only"]
    assert report["ratings_packet_bound"]
    ratings["ratings"]["A"]["facts_preserved"] = None
    ratings_path.write_text(json.dumps(ratings))
    with pytest.raises(ValueError, match="boolean observation"):
        review.grade(folder, ratings_path)
    ratings["ratings"]["A"]["facts_preserved"] = True
    ratings_path.write_text(json.dumps(ratings))
    with (folder / "reviewer" / "review-packet.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="packet changed"):
        review.grade(folder, ratings_path)


def test_only_reviewer_directory_cannot_grade_or_disclose_mapping(trial, tmp_path):
    root, _ = trial
    folder = tmp_path / "blind"
    review.prepare(root, folder)
    reviewer = folder / "reviewer"
    ratings = review._load_json(reviewer / "ratings-template.json")
    ratings["reviewer"] = "separate-reviewer"
    for checks in ratings["ratings"].values():
        for check in checks:
            checks[check] = True
    ratings_path = reviewer / "completed-ratings.json"
    ratings_path.write_text(json.dumps(ratings))
    with pytest.raises(FileNotFoundError):
        review.grade(reviewer, ratings_path)
    assert review.grade(folder, ratings_path)["reviewer"] == "separate-reviewer"


def test_grade_accepts_legacy_private_folder_layout_without_emitting_it(trial, tmp_path):
    root, _ = trial
    folder = tmp_path / "blind"
    review.prepare(root, folder)
    (folder / "reviewer" / "review-packet.json").replace(folder / "review-packet.json")
    (folder / "private" / "condition-key.json").replace(folder / "condition-key.json")
    ratings = review._load_json(folder / "reviewer" / "ratings-template.json")
    ratings["reviewer"] = "historic-packet"
    for checks in ratings["ratings"].values():
        for check in checks:
            checks[check] = True
    ratings_path = tmp_path / "legacy-ratings.json"
    ratings_path.write_text(json.dumps(ratings))
    assert review.grade(folder, ratings_path)["reviewer"] == "historic-packet"


def test_ratings_from_a_different_packet_are_not_reused(trial, tmp_path):
    root, _ = trial
    first = tmp_path / "review-one"
    second = tmp_path / "review-two"
    review.prepare(root, first)
    review.prepare(root, second)
    ratings = review._load_json(first / "reviewer" / "ratings-template.json")
    ratings["reviewer"] = "independent-review"
    for checks in ratings["ratings"].values():
        for check in checks:
            checks[check] = True
    submitted = tmp_path / "submitted.json"
    submitted.write_text(json.dumps(ratings))
    assert review.grade(first, submitted)["ratings_packet_bound"] is True
    with pytest.raises(ValueError, match="not bound to this review packet"):
        review.grade(second, submitted)
    del ratings["review_packet_sha256"]
    submitted.write_text(json.dumps(ratings))
    with pytest.raises(ValueError, match="not bound to this review packet"):
        review.grade(first, submitted)
    legacy = review.grade(first, submitted, allow_legacy_unbound=True)
    assert legacy["ratings_packet_bound"] is False


def test_review_rejects_differing_prompts_before_export(trial, tmp_path):
    root, record = trial
    for item in record["protocol"]:
        if item["stage"] == "conditional_transfer":
            item["prompt"] = "A condition-revealing alternative task"
    (root / "ordinary-transfer.json").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="paired review prompts differ"):
        review.prepare(root, tmp_path / "unsafe-review")
    assert not (tmp_path / "unsafe-review").exists()
