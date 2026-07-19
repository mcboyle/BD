"""Phase 9.14 -- N-step login inference, assist only (RED-first)."""
from bulk_downloader import login_assist

def test_email_first_step():
    out=login_assist.infer_step({"fields":["email"]})
    assert out["next_step"]=="enter_email"

def test_password_step():
    out=login_assist.infer_step({"fields":["password"]})
    assert out["next_step"]=="enter_password"

def test_sso_cross_origin_review():
    out=login_assist.infer_step({"buttons":["Sign in with Google"],"cross_origin":True})
    assert out["next_step"]=="sso_review" and out["requires_review"] is True

def test_challenge_manual_handoff():
    out=login_assist.infer_step({"challenge":True,"fields":["password"]})
    assert out["next_step"]=="manual_handoff" and out["requires_review"] is True

def test_unrecognized_routes_to_review():
    out=login_assist.infer_step({"fields":[]})
    assert out["requires_review"] is True

def test_cannot_persist_credentials_or_enable():
    assert not hasattr(login_assist,"persist")
    assert not hasattr(login_assist,"save_credentials")
    assert not hasattr(login_assist,"enable_host")
    out=login_assist.infer_step({"fields":["email","password"]})
    assert "password" not in str(out.get("summary","")).lower() or out["advisory"] is True
    assert out["advisory"] is True
