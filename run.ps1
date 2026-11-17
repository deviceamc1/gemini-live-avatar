# PowerShell launch script for Gemini Live Avatar
param (
    [string]$AvatarPath = "/avatars/brunette.glb",
    [string]$ResponseModality = "audio",
    [int]$Port = 8080
)

Write-Host "Starting Gemini Live Avatar on http://127.0.0.1:$Port ..." -ForegroundColor Cyan

.\.venv\Scripts\gemini-live-avatar.exe `
    --google-search-grounding `
    --workers 1 `
    --avatar-path $AvatarPath `
    --response-modality $ResponseModality `
    --port $Port
