from pydantic import BaseModel, ConfigDict, Field


class PreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic_ids: list[str] = Field(max_length=1500)


class BookmarkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bookmarked: bool


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8000)
