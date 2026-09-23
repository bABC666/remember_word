from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WordSummary(ORMModel):
    id: int
    word: str
    phonetic: str
    part_of_speech: str
    source_meanings: list[str]
    source_raw: str
    anchor: str
    semantic_note: str
    status: str
    first_seen: datetime
    last_review: datetime | None
    next_review_at: datetime | None
    recall_success: int
    recall_fail: int
    context_exposure: int
    possible_issue: bool
    notes: str


class ReviewRequest(BaseModel):
    result: Literal["know", "fuzzy", "fail"]
    source: Literal["daily", "reading", "library"] = "daily"
    review_type: str = "recall"
    article_id: int | None = None


class CandidateUpdate(BaseModel):
    word: str | None = None
    phonetic: str | None = None
    part_of_speech: str | None = None
    source_meanings: list[str] | None = None
    source_raw: str | None = None
    anchor: str | None = None
    semantic_note: str | None = None
    possible_issue: bool | None = None
    issue_note: str | None = None
    selected: bool | None = None


class ConfirmCandidatesRequest(BaseModel):
    candidate_ids: list[int]


class ArticleGenerateRequest(BaseModel):
    target_count: int = Field(default=20, ge=1, le=30)
    length: int | None = Field(default=None, ge=300, le=1200)


class ArticleJudgeRequest(BaseModel):
    #: The caller's own ``user_word_state.id``. Explicitly the state namespace: a
    #: legacy ``word.id`` cannot address a word that was added from an article,
    #: because that word has no ``word`` row.
    word_state_id: int
    user_meaning: str = Field(min_length=1, max_length=1000)


class ArticleLookupRequest(BaseModel):
    word: str = Field(min_length=1, max_length=160)


class SettingsUpdate(BaseModel):
    deepseek_api_key: str | None = Field(default=None, max_length=500)
    deepseek_base_url: str | None = Field(default=None, max_length=500)
    deepseek_model: str | None = Field(default=None, max_length=200)
    daily_new_words: int | None = Field(default=None, ge=1, le=100)
    article_length: int | None = Field(default=None, ge=300, le=1200)
    ocr_language: str | None = Field(default=None, max_length=30)
    ocr_use_gpu: bool | None = None


class APIMessage(BaseModel):
    message: str
    detail: Any | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class SessionRevokeRequest(BaseModel):
    """Bulk session revocation: "sign out my other devices", or all of them.

    ``scope`` is a closed set rather than a free string, and the only other field is
    the password the re-auth guard demands. Nothing here can influence the audit
    trail: the event's ``action`` is chosen by the endpoint, not sent by the client.
    """

    scope: Literal["others", "all"]
    current_password: str = Field(min_length=1, max_length=256)


class SessionDeleteRequest(BaseModel):
    """Revoking one device: the same re-auth guard as the bulk action.

    Deleting a session changes the set of credentials that can reach the account,
    so the phase 2.7-d rule applies to it as well. One field only, so nothing a
    caller sends can reach the audit trail.
    """

    current_password: str = Field(min_length=1, max_length=256)


class CreateUserRequest(BaseModel):
    """Create an account, behind the administrator's own password.

    Two different secrets appear in this body and they must not share a name:
    ``password`` is the **new account's**, while ``current_password`` is the
    **caller's own** -- the value the re-auth guard verifies (Phase 2.8 S-1).
    Creating an account is how a stolen session would install a permanent
    administrator, so the guard runs before anything is written.
    """

    username: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=120)
    password: str = Field(min_length=8, max_length=256)
    role: Literal["admin", "user"] = "user"
    current_password: str = Field(min_length=1, max_length=256)


class UpdateUserRequest(BaseModel):
    """Update an account, behind the administrator's own password.

    Every field here is something the phase 2.7-d rule calls sensitive -- display
    identity, capability (``role``), whether the account can be reached at all
    (``is_active``), and the credential itself (``password``) -- so *every* call to
    this endpoint demands ``current_password``, including one that changes nothing.
    Which account fields were actually sent is still decided by ``exclude_unset``;
    ``current_password`` is the admin's own password and takes no part in that.

    ``password`` is the **target account's** new password. ``current_password`` is
    the **caller's** own, and is therefore a required field rather than an optional
    one: an absent password must be a refusal, never a silent fallback to
    session-only authorisation.
    """

    display_name: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None
    role: Literal["admin", "user"] | None = None
    password: str | None = Field(default=None, min_length=8, max_length=256)
    current_password: str = Field(min_length=1, max_length=256)


class LexiconCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class LexiconUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    visibility: Literal["public", "private"] | None = None
