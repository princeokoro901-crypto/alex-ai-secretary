"""Shared fake Google sender factory used only by focused tests."""
from cryptography.fernet import Fernet
from app.connectors.gmail_sender import GoogleGmailEmailProvider
from app.connectors.email_provider import EmailProviderRegistry
from app.connectors.google_oauth import GOOGLE_REQUIRED_SCOPES, GoogleOAuthAdapter
from app.connectors.oauth_security import OAuthAuthorizationService, OAuthCredentialVault


class FakeGoogleTransport:
    def __init__(self):
        self.gmail_calls = []

    def post_form(self, url, form):
        if form.get("grant_type") == "refresh_token":
            return {"access_token": "ACCESS_REFRESHED", "token_type": "Bearer", "expires_in": 7200}
        return {"access_token": "ACCESS_SECRET", "refresh_token": "REFRESH_SECRET", "token_type": "Bearer",
                "expires_in": 3600, "scope": " ".join(GOOGLE_REQUIRED_SCOPES)}

    def get_bearer_json(self, url, access_token):
        return {"sub": "google-sub-123", "email": "sender@example.com", "email_verified": True}

    def revoke_token(self, token):
        return None

    def send_gmail_message(self, access_token, raw_message):
        self.gmail_calls.append((access_token, raw_message))
        return {"id": "gmail-message-123", "threadId": "thread-123"}


def make_connected_sender(tmp_path):
    owner_actor_id, owner_key_id = "account-owner", "owner-key"
    key = Fernet.generate_key()
    transport = FakeGoogleTransport()
    auth = OAuthAuthorizationService(tmp_path / "state.sqlite3", key,
                                     {"gmail": "https://app.example.test/oauth/google/callback"}, clock=lambda: 1000.0)
    vault = OAuthCredentialVault(tmp_path / "credentials.sqlite3", key, clock=lambda: 1000.0)
    oauth = GoogleOAuthAdapter(client_id="client-id", client_secret="client-secret",
                               authorization_service=auth, credential_vault=vault,
                               enabled=True, transport=transport, clock=lambda: 1000.0)
    started = oauth.begin(owner_actor_id, owner_key_id)
    oauth.complete(code="one-time-code", state=started["state"], actor_id=owner_actor_id, key_id=owner_key_id)
    provider = GoogleGmailEmailProvider(oauth, owner_actor_id=owner_actor_id,
                                        account_id="google-sub-123", ledger_path=str(tmp_path / "gmail-ledger.sqlite3"))
    registry = EmailProviderRegistry(real_sending_enabled=True, allowed_provider_ids=("gmail",))
    registry.register("gmail", provider)
    return oauth, transport, provider, registry
