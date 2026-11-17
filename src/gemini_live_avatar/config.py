import typing
from pydantic import BaseModel

class RuntimeConfig(BaseModel):
    google_search_grounding: bool = False
    tts_lang: str = "en-US"
    tts_voice: str = "en-GB-Standard-A"
    avatar_path: str = "/avatars/brunette.glb"
    model_name: str = "gemini-2.5-flash-native-audio-latest"#"gemini-live-2.5-flash-preview"
    mcp_server_config: typing.Optional[str] = None
    response_modality: str = "audio"  # "text", "audio", or "both"
