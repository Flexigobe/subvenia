"""Tests for the Brevo email client."""

import pytest

from app.lib.email_brevo import send_email


@pytest.mark.asyncio
async def test_send_email_log_only_when_no_key(monkeypatch, caplog):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "brevo_api_key", "")

    import logging
    with caplog.at_level(logging.INFO, logger="app.lib.email_brevo"):
        result = await send_email("x@y.com", "Subj", "<p>Hi</p>")
    assert result is True
    assert any("LOG-ONLY" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_send_email_calls_brevo_api(monkeypatch, httpx_mock):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "brevo_api_key", "fake-key")
    monkeypatch.setattr(get_settings(), "alert_from_email", "from@example.com")

    httpx_mock.add_response(
        url="https://api.brevo.com/v3/smtp/email",
        method="POST",
        json={"messageId": "abc"},
    )

    result = await send_email("to@example.com", "S", "<p>B</p>")
    assert result is True
    req = httpx_mock.get_request()
    body = req.read().decode()
    assert "to@example.com" in body
    assert "from@example.com" in body


@pytest.mark.asyncio
async def test_send_email_attaches_files(monkeypatch, httpx_mock):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "brevo_api_key", "fake-key")

    httpx_mock.add_response(
        url="https://api.brevo.com/v3/smtp/email",
        method="POST",
        json={"messageId": "abc"},
    )

    attachments = [{"filename": "doc.pdf", "base64": "QUJDREU=", "content_type": "application/pdf"}]
    result = await send_email("to@e.com", "S", "B", attachments=attachments)
    assert result is True
    body = httpx_mock.get_request().read().decode()
    assert "doc.pdf" in body
    assert "QUJDREU=" in body


@pytest.mark.asyncio
async def test_send_email_raises_on_4xx(monkeypatch, httpx_mock):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "brevo_api_key", "fake-key")
    httpx_mock.add_response(
        url="https://api.brevo.com/v3/smtp/email",
        method="POST",
        status_code=401,
        json={"message": "Invalid key"},
    )

    import httpx
    with pytest.raises(httpx.HTTPStatusError):
        await send_email("to@e.com", "S", "B")
