import json
from pathlib import Path


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_repo(root: Path) -> Path:
    prompt = root / "codex-rs" / "models-manager" / "prompt.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text("You are Codex. Use tools carefully.", encoding="utf-8")
    return root


def basic_pi_document(base_url: str = "https://pi.test/v1") -> dict[str, object]:
    return {
        "providers": {
            "openai-codex": {
                "models": [{"id": "gpt-private", "name": "Do not duplicate"}]
            },
            "demo": {
                "api": "openai-completions",
                "baseUrl": base_url,
                "compat": {"supportsDeveloperRole": False},
                "models": [
                    {
                        "id": "demo/model",
                        "name": "Demo Model",
                        "reasoning": True,
                        "input": ["text", "image"],
                        "contextWindow": 131072,
                        "maxTokens": 8192,
                        "compat": {
                            "reasoningEffortMap": {
                                "low": "low",
                                "medium": "high",
                                "high": "max",
                            }
                        },
                    }
                ],
            },
        }
    }
